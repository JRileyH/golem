import json
import subprocess
from pathlib import Path

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from deployer import compose
from deployer.app import create_app
from deployer.audit import Audit
from deployer.config import Settings
from deployer.deploy import Deployer
from deployer.source import GitHubError, GitHubSource
from deployer.validate import Policy, Project, Rejected

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "s" * 40
ADMIN_SECRET = "a" * 40
SHA = "0123456789abcdef0123456789abcdef01234567"
AUTH = {"X-Deployer-Secret": SECRET}
ADMIN = {"X-Deployer-Admin-Secret": ADMIN_SECRET}


class FakeSource:
    def __init__(self, text: str, failing: list[str] | None = None):
        self.text = text
        self.failing = failing or []

    def compose_file(self, slug, sha):
        return self.text

    def failing_checks(self, slug, sha):
        return self.failing


class FakeDocker(compose.Docker):
    def __init__(self, projects_dir: Path):
        super().__init__(projects_dir, "always")
        self.calls = []

    def up(self, slug, text, override):
        self.calls.append(("up", slug, override))
        self.dir(slug).mkdir(parents=True, exist_ok=True)

    def down(self, slug, volumes=False):
        self.calls.append(("down", slug, volumes))

    def ps(self, slug):
        return [{"service": "web", "state": "running", "status": "Up 1 second"}]

    def logs(self, slug, service, tail):
        return f"logs for {service or 'all'}, last {tail}"


class Harness:
    def __init__(self, tmp: Path, source: FakeSource, verify=lambda slug: None, require_checks=True, admin_secret=ADMIN_SECRET):
        self.settings = Settings(
            secret=SECRET,
            base_domain="unlaunched.dev",
            git_source="github",
            org="unlaunched",
            github_token="token",
            registry_user="riggy",
            registry_token="token",
            require_checks=require_checks,
            pull_policy="always",
            data_dir=tmp,
            routes_dir=tmp / "routes",
            git_dir=tmp / "git",
            traefik_url="http://traefik:8080",
            policy=Policy(),
        )
        self.docker = FakeDocker(tmp / "projects")
        self.audit_path = tmp / "audit.jsonl"
        deployer = Deployer(self.settings, source, self.docker, verify)
        self.client = TestClient(create_app(deployer, Audit(self.audit_path), SECRET, admin_secret))

    def deploy(self, slug="hello", sha=SHA, headers=AUTH):
        return self.client.post("/deploy", json={"slug": slug, "sha": sha}, headers=headers)

    def delete(self, slug="hello", headers=ADMIN):
        return self.client.post("/delete", json={"slug": slug}, headers=headers)

    def audit(self) -> list[dict]:
        return [json.loads(line) for line in self.audit_path.read_text().splitlines()]


@pytest.fixture
def valid():
    return (FIXTURES / "valid.yml").read_text()


def test_every_call_needs_the_secret(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    assert h.deploy(headers={}).status_code == 401
    assert h.deploy(headers={"X-Deployer-Secret": "wrong"}).status_code == 401
    assert h.client.get("/status", params={"slug": "hello"}).status_code == 401
    assert h.docker.calls == []
    assert [e["outcome"] for e in h.audit()] == ["unauthorized"] * 3


def test_healthz_is_open(tmp_path, valid):
    assert Harness(tmp_path, FakeSource(valid)).client.get("/healthz").json() == {"ok": True}


def test_deploy_writes_override_route_and_state(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    r = h.deploy()
    assert r.status_code == 200
    assert r.json() == {"ok": True, "url": "https://hello.unlaunched.dev", "sha": SHA}

    _, slug, override = h.docker.calls[0]
    assert slug == "hello"
    assert set(override["services"]) == {"web", "db", "cache"}
    assert override["services"]["db"]["labels"] == {"traefik.enable": "false", "golem.slug": "hello"}
    assert override["services"]["web"]["networks"]["golem-apps"] == {"aliases": ["hello.golem"]}
    assert "networks" not in override["services"]["db"]
    assert "image" not in override["services"]["web"]  # already tagged in the fixture

    route = yaml.safe_load((tmp_path / "routes" / "hello.yml").read_text())
    assert route["http"]["routers"]["hello"]["rule"] == "Host(`hello.unlaunched.dev`)"
    assert route["http"]["services"]["hello"]["loadBalancer"]["servers"] == [{"url": "http://hello.golem:3000"}]

    state = json.loads((tmp_path / "projects" / "hello" / "deployed.json").read_text())
    assert state["sha"] == SHA and state["port"] == 3000
    assert h.audit()[-1] == {**h.audit()[-1], "action": "deploy", "outcome": "ok", "slug": "hello", "sha": SHA}


@pytest.mark.parametrize(
    "slug, sha, reason",
    [
        ("Hello", SHA, "slug must match ^[a-z0-9][a-z0-9-]{1,30}$ and not end in `-`"),
        ("golem", SHA, "slug `golem` is reserved"),
        ("hello", "main", "sha must be a full 40-character lowercase commit hash"),
    ],
)
def test_deploy_rejects_bad_slug_or_sha(tmp_path, valid, slug, sha, reason):
    h = Harness(tmp_path, FakeSource(valid))
    r = h.deploy(slug, sha)
    assert r.status_code == 422
    assert r.json()["reasons"] == [reason]
    assert h.docker.calls == []
    assert h.audit()[-1]["reasons"] == [reason]


def test_deploy_accepts_only_slug_and_sha(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    r = h.client.post("/deploy", json={"slug": "hello", "sha": SHA, "mode": "reckless"}, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["reasons"] == ["body must be a JSON object with exactly: sha, slug"]


def test_deploy_requires_passing_checks(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid, failing=["build: failure", "test: in_progress"]))
    r = h.deploy()
    assert r.status_code == 422
    assert r.json()["reasons"] == ["checks have not all passed on 0123456: build: failure, test: in_progress"]
    assert h.docker.calls == []


def test_checks_are_skipped_when_not_required(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid, failing=["build: failure"]), require_checks=False)
    assert h.deploy().status_code == 200


def test_deploy_rejects_invalid_compose(tmp_path):
    h = Harness(tmp_path, FakeSource((FIXTURES / "privileged.yml").read_text()))
    r = h.deploy()
    assert r.status_code == 422
    assert r.json()["reasons"] == ["service `web`: `privileged` is not allowed"]
    assert h.docker.calls == []
    assert not (tmp_path / "routes" / "hello.yml").exists()


def test_failed_verification_returns_logs_and_keeps_state(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid), verify=lambda slug: "HTTP 502")
    r = h.deploy()
    assert r.status_code == 502
    assert r.json() == {
        "ok": False,
        "error": "https://hello.unlaunched.dev did not respond: HTTP 502",
        "logs": "logs for all, last 50",
    }
    # Still recorded, so the agent can read logs and stop it.
    assert h.client.get("/logs", params={"slug": "hello"}, headers=AUTH).status_code == 200


def test_stop_keeps_volumes_and_removes_route(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    h.deploy()
    r = h.client.post("/stop", json={"slug": "hello"}, headers=AUTH)
    assert r.json() == {"ok": True, "stopped": "hello"}
    assert h.docker.calls[-1] == ("down", "hello", False)
    assert not (tmp_path / "routes" / "hello.yml").exists()


def test_stop_needs_a_deployed_project(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    r = h.client.post("/stop", json={"slug": "hello"}, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["reasons"] == ["`hello` has not been deployed"]


def test_delete_takes_the_volumes_route_and_deploy_files(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    h.deploy()
    r = h.delete()
    assert r.json() == {"ok": True, "deleted": "hello"}
    assert h.docker.calls[-1] == ("down", "hello", True)
    assert not (tmp_path / "routes" / "hello.yml").exists()
    assert not (tmp_path / "projects" / "hello").exists()
    assert h.audit()[-1] == {**h.audit()[-1], "action": "delete", "outcome": "ok", "slug": "hello"}
    assert h.client.get("/status", params={"slug": "hello"}, headers=AUTH).json()["deployed"] is None


def test_delete_needs_the_admin_secret(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    h.deploy()
    for headers in ({}, AUTH, {"X-Deployer-Admin-Secret": SECRET}):
        r = h.delete(headers=headers)
        assert r.status_code == 401
        assert r.json()["reasons"] == ["missing or wrong X-Deployer-Admin-Secret"]
    assert [c[0] for c in h.docker.calls] == ["up"]
    assert [e["outcome"] for e in h.audit() if e["action"] == "delete"] == ["unauthorized"] * 3


def test_delete_is_off_without_an_admin_secret(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid), admin_secret="")
    h.deploy()
    for headers in ({}, {"X-Deployer-Admin-Secret": ""}, AUTH):
        r = h.delete(headers=headers)
        assert r.status_code == 403
        assert r.json()["reasons"] == ["deleting is off: DEPLOYER_ADMIN_SECRET is not set"]
    assert [c[0] for c in h.docker.calls] == ["up"]


def test_delete_needs_a_deployed_project(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    assert h.delete().json()["reasons"] == ["`hello` has not been deployed"]
    assert h.delete("golem").json()["reasons"] == ["slug `golem` is reserved"]
    assert h.delete("../routes").json()["reasons"] == ["slug must match ^[a-z0-9][a-z0-9-]{1,30}$ and not end in `-`"]
    assert h.docker.calls == []


def test_delete_works_on_a_stopped_project(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    h.deploy()
    h.client.post("/stop", json={"slug": "hello"}, headers=AUTH)
    assert h.delete().json() == {"ok": True, "deleted": "hello"}
    assert h.docker.calls[-1] == ("down", "hello", True)


def test_projects_lists_running_and_stopped_projects(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    assert h.client.get("/projects", headers=AUTH).json() == {"ok": True, "projects": []}
    h.deploy("trees")
    h.deploy("hello")
    h.client.post("/stop", json={"slug": "trees"}, headers=AUTH)
    assert h.client.get("/projects", headers=AUTH).json() == {"ok": True, "projects": ["hello", "trees"]}
    assert h.client.get("/projects").status_code == 401


LOCAL_ENV = {"DEPLOYER_SECRET": SECRET, "DEPLOYER_GIT_SOURCE": "local", "BASE_DOMAIN": "localhost", "GITHUB_ORG": "me"}


@pytest.mark.parametrize("admin", ["short", SECRET])
def test_admin_secret_must_be_long_and_its_own(admin):
    with pytest.raises(SystemExit, match="DEPLOYER_ADMIN_SECRET must be"):
        Settings.from_env({**LOCAL_ENV, "DEPLOYER_ADMIN_SECRET": admin})


@pytest.mark.parametrize("missing", ["BASE_DOMAIN", "GITHUB_ORG"])
def test_domain_and_org_are_required(missing):
    with pytest.raises(SystemExit, match="BASE_DOMAIN and GITHUB_ORG must be set"):
        Settings.from_env({k: v for k, v in LOCAL_ENV.items() if k != missing})


def test_admin_secret_is_optional():
    assert Settings.from_env(LOCAL_ENV).admin_secret == ""
    assert Settings.from_env({**LOCAL_ENV, "DEPLOYER_ADMIN_SECRET": ADMIN_SECRET}).admin_secret == ADMIN_SECRET


def test_only_delete_passes_volumes_to_compose(tmp_path, monkeypatch):
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(compose.subprocess, "run", run)
    docker = compose.Docker(tmp_path, "always")
    docker.down("hello")
    docker.down("hello", volumes=True)
    assert [c[c.index("down") :] for c in commands] == [
        ["down", "--remove-orphans"],
        ["down", "--remove-orphans", "--volumes"],
    ]


def test_status(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    assert h.client.get("/status", params={"slug": "hello"}, headers=AUTH).json() == {
        "ok": True,
        "slug": "hello",
        "deployed": None,
        "route": None,
        "containers": [],
    }
    h.deploy()
    status = h.client.get("/status", params={"slug": "hello"}, headers=AUTH).json()
    assert status["deployed"]["sha"] == SHA
    assert status["route"] == "https://hello.unlaunched.dev"
    assert status["containers"][0]["state"] == "running"


def test_logs(tmp_path, valid):
    h = Harness(tmp_path, FakeSource(valid))
    h.deploy()
    get = lambda **q: h.client.get("/logs", params={"slug": "hello", **q}, headers=AUTH)
    assert get(service="db", tail="20").json()["logs"] == "logs for db, last 20"
    assert get(service="traefik").json()["reasons"] == ["`hello` has no service `traefik` (services: web, db, cache)"]
    assert get(tail="5000").json()["reasons"] == ["tail must be a number from 1 to 1000"]


def test_route_and_override_shapes():
    project = Project(("web",), "web", 80)
    assert compose.route("hello", 80, "localhost") == {
        "http": {
            "routers": {"hello": {"rule": "Host(`hello.localhost`)", "service": "hello"}},
            "services": {"hello": {"loadBalancer": {"servers": [{"url": "http://hello.golem:80"}]}}},
        }
    }
    assert compose.override("hello", SHA, "unlaunched", project)["networks"] == {
        "golem-apps": {"external": True, "name": "golem-apps"}
    }


def test_own_untagged_images_are_pinned_to_the_commit():
    images = {
        "web": "ghcr.io/unlaunched/hello",
        "worker": "ghcr.io/unlaunched/hello/worker",
        "tagged": "ghcr.io/unlaunched/hello:v1",
        "other": "ghcr.io/unlaunched/trees",
        "lookalike": "ghcr.io/unlaunched/hello-admin",
        "db": "postgres:18",
    }
    project = Project(tuple(images), "web", 80, images)
    services = compose.override("hello", SHA, "unlaunched", project)["services"]
    assert services["web"]["image"] == f"ghcr.io/unlaunched/hello:{SHA}"
    assert services["worker"]["image"] == f"ghcr.io/unlaunched/hello/worker:{SHA}"
    for name in ("tagged", "other", "lookalike", "db"):
        assert "image" not in services[name]


# --- GitHub source ---------------------------------------------------------------


def github(handler) -> GitHubSource:
    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    return GitHubSource("unlaunched", "token", client)


def test_github_compose_file():
    def handler(request):
        assert request.url.path == "/repos/unlaunched/hello/contents/deploy/compose.yml"
        assert request.url.params["ref"] == SHA
        return httpx.Response(200, text="services: {}\n")

    assert github(handler).compose_file("hello", SHA) == "services: {}\n"


def test_github_compose_file_missing():
    with pytest.raises(Rejected) as e:
        github(lambda request: httpx.Response(404)).compose_file("hello", SHA)
    assert e.value.reasons == [f"deploy/compose.yml not found in unlaunched/hello at {SHA}; is the commit pushed?"]


def run(id, workflow_id, name, status, conclusion):
    return {"id": id, "workflow_id": workflow_id, "name": name, "status": status, "conclusion": conclusion}


@pytest.mark.parametrize(
    "runs, failing",
    [
        ([], ["no workflow runs on this commit yet; wait for CI to start"]),
        ([run(1, 10, "build", "completed", "success")], []),
        (
            [
                run(1, 10, "build", "completed", "success"),
                run(2, 11, "lint", "completed", "skipped"),
                run(3, 12, "test", "in_progress", None),
                run(4, 13, "e2e", "completed", "failure"),
            ],
            ["test: in_progress", "e2e: failure"],
        ),
        # The latest run of each workflow counts, whatever order GitHub lists them in.
        ([run(5, 10, "build", "completed", "success"), run(1, 10, "build", "completed", "failure")], []),
        ([run(1, 10, "build", "completed", "success"), run(5, 10, "build", "queued", None)], ["build: queued"]),
    ],
)
def test_github_failing_checks(runs, failing):
    def handler(request):
        assert request.url.path == "/repos/unlaunched/hello/actions/runs"
        assert request.url.params["head_sha"] == SHA
        return httpx.Response(200, json={"total_count": len(runs), "workflow_runs": runs})

    assert github(handler).failing_checks("hello", SHA) == failing


def test_github_errors_explain_themselves():
    def handler(request):
        return httpx.Response(403, json={"message": "Resource not accessible by personal access token"})

    with pytest.raises(GitHubError) as e:
        github(handler).failing_checks("hello", SHA)
    assert str(e.value) == (
        "GitHub returned 403 reading workflow runs; Resource not accessible by personal access token; "
        "DEPLOYER_GITHUB_TOKEN lacks a permission, or the rate limit was hit"
    )
