"""Golem settings, read once from the environment at startup."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path

from .store import EFFORTS, MODELS


@dataclass(frozen=True)
class Settings:
    base_domain: str
    edge_port: str  # Traefik's host port when it doesn't hold 80 and 443; only local links use it
    harness: str  # "claude" or "fake"
    org: str
    deployer_url: str
    state_dir: Path  # SQLite, run logs, lessons.md
    workspace_dir: Path  # a checkout per project, and a worktree per job in .jobs/ (workspace.TREES)
    process_dir: Path  # built-in workflows, skills and prompts, read-only
    # Signs Riggy in on the web app. No default: it's set in .env or golem won't start.
    web_password: str
    # What a new project gets unless the dictation or a command says otherwise.
    default_model: str = "opus"
    default_effort: str = "high"
    # What CI builds project images for: this box's own architecture.
    platform: str = "linux/amd64"
    # Scratch space: each job's TMPDIR is a directory of its own in here (job_tmp).
    tmp_dir: Path = Path("/tmp/golem")

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

    def lessons(self, slug: str = "") -> Path:
        """The lessons file workers read and add to: golem's, or a project's own (one that runs in a
        vacuum has one, starting empty, as on a fresh golem)."""
        return self.state_dir / "lessons" / f"{slug}.md" if slug else self.state_dir / "lessons.md"

    def job_logs(self, job_id: int) -> Path:
        """Where a job's runs are logged, one compressed event stream per run."""
        return self.state_dir / "jobs" / str(job_id)

    def job_claude(self, job_id: int) -> Path:
        """A job's own Claude Code configuration, empty when it starts, for a project in a vacuum:
        no settings, memory or history from any other run (Scheduler.worker_env)."""
        return self.tmp_dir / f"claude-{job_id}"

    def job_tmp(self, job_id: int) -> Path:
        """A job's own scratch directory, its workers' TMPDIR: workers running beside it have theirs.
        Per job rather than per run, so a session resumed later finds its files."""
        return self.tmp_dir / f"job-{job_id}"


# Where the container's memory cap (GOLEM_MEM_LIMIT, in compose.yml) shows inside it: cgroup v2, then v1.
CGROUP_LIMITS = (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"))


def memory_cap(paths: tuple[Path, ...] = CGROUP_LIMITS) -> int | None:
    """golem's memory cap in bytes, shared by golem and every worker it runs; None if it has
    none (or isn't in a container). GOLEM_MEM_LIMIT itself never reaches the container."""
    for path in paths:
        try:
            text = path.read_text().strip()
        except OSError:
            continue
        if not text.isdigit():  # "max": no cap
            return None
        return int(text) if int(text) < 1 << 60 else None  # cgroup v1 says "none" with a huge number
    return None


def _platform() -> str:
    """The Docker platform of the machine golem runs on (a container shares the host's)."""
    return "linux/arm64" if platform.machine() in ("aarch64", "arm64") else "linux/amd64"


def _choice(env: dict[str, str], name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = env.get(name) or default
    if value not in allowed:
        raise SystemExit(f"{name} must be one of {', '.join(allowed)}")
    return value
