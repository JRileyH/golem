"""golem's own calls to the deployer. Workers only read from it (`golem-deploy`)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

log = logging.getLogger(__name__)

NOT_DEPLOYED = "has not been deployed"
DEPLOY_TIMEOUT = 900  # pulling images and waiting for the site to answer


class DeployerError(Exception):
    """The deployer couldn't be reached or isn't set up right: not the project's fault."""


@dataclass(frozen=True)
class Deployment:
    ok: bool
    url: str = ""
    problem: str = ""  # why it didn't deploy, for a worker to fix: rejected rules, or the app's logs


class Deployer(Protocol):
    async def deploy(self, slug: str, sha: str) -> Deployment: ...
    async def stop(self, slug: str) -> None: ...
    async def delete(self, slug: str) -> None: ...
    async def projects(self) -> list[str]: ...


class DeployerClient:
    def __init__(self, url: str, secret: str, admin_secret: str = "", client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(base_url=url, headers={"X-Deployer-Secret": secret}, timeout=120)
        self.admin_secret = admin_secret

    async def deploy(self, slug: str, sha: str) -> Deployment:
        """Deploy a pushed commit whose checks passed."""
        try:
            r = await self.client.post("/deploy", json={"slug": slug, "sha": sha}, timeout=DEPLOY_TIMEOUT)
            body = r.json()
        except httpx.HTTPError as e:
            raise DeployerError(f"the deployer didn't answer ({type(e).__name__})") from None
        except ValueError:
            raise DeployerError(f"the deployer said {r.status_code}") from None
        if body.get("ok"):
            return Deployment(True, url=body.get("url", ""))
        if r.status_code == 422:  # the project broke a rule
            return Deployment(False, problem="\n".join(body.get("reasons") or []))
        if r.status_code == 502 and "logs" in body:  # it started but didn't answer
            return Deployment(False, problem=f"{body.get('error')}\n\nLogs:\n{str(body['logs'])[-4000:]}")
        raise DeployerError(_problem(r))

    async def stop(self, slug: str) -> None:
        """Stop a project, keeping its data. Not having been deployed is fine."""
        try:
            r = await self.client.post("/stop", json={"slug": slug})
        except httpx.HTTPError as e:
            log.warning("stopping %s: %s", slug, e)
            return
        if r.status_code != 200 and NOT_DEPLOYED not in r.text:
            log.warning("stopping %s: %s %s", slug, r.status_code, r.text[:300])

    async def delete(self, slug: str) -> None:
        """Take a project down and delete its data and route. Not having been deployed is fine."""
        try:
            r = await self.client.post(
                "/delete", json={"slug": slug}, headers={"X-Deployer-Admin-Secret": self.admin_secret}
            )
        except httpx.HTTPError as e:
            raise DeployerError(f"the deployer didn't answer ({type(e).__name__})") from None
        if r.status_code != 200 and NOT_DEPLOYED not in r.text:
            raise DeployerError(_problem(r))

    async def projects(self) -> list[str]:
        """Every project the deployer has deploy files for, running or stopped."""
        try:
            r = await self.client.get("/projects")
        except httpx.HTTPError as e:
            raise DeployerError(f"the deployer didn't answer ({type(e).__name__})") from None
        if r.status_code != 200:
            raise DeployerError(_problem(r))
        return r.json()["projects"]


class OfflineDeployer:
    """For HARNESS=fake: every deploy works and nothing is running."""

    def __init__(self, url: str):
        self.url = url

    async def deploy(self, slug: str, sha: str) -> Deployment:
        return Deployment(True, url=self.url.format(slug=slug))

    async def stop(self, slug: str) -> None:
        pass

    async def delete(self, slug: str) -> None:
        pass

    async def projects(self) -> list[str]:
        return []


def _problem(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return f"the deployer said {r.status_code}"
    return "; ".join(body.get("reasons") or [body.get("error") or f"the deployer said {r.status_code}"])
