from pathlib import Path

import pytest

from deployer.validate import (
    Policy,
    Project,
    Rejected,
    check_sha,
    check_slug,
    validate_compose,
)

FIXTURES = Path(__file__).parent / "fixtures"
OFFICIAL = "caddy, httpd, mariadb, memcached, mongo, mysql, nginx, postgres, redis"
BIND = "use a named volume declared under top-level volumes:"

# One bad fixture per rule, and the exact reasons it must produce.
REJECTIONS = {
    "build.yml": [
        "service `web`: `build` is not allowed (build the image in CI and push it to ghcr.io/unlaunched/)"
    ],
    "image-docker-hub.yml": [
        f"service `web`: image `someone/app:1` is not allowed; use ghcr.io/unlaunched/<name> or an official image ({OFFICIAL})"
    ],
    "image-other-org.yml": [
        f"service `web`: image `ghcr.io/evil/app:1` is not allowed; use ghcr.io/unlaunched/<name> or an official image ({OFFICIAL})"
    ],
    "bind-mount.yml": [f"service `web`: bind mounts are not allowed (`./data:/data`); {BIND}"],
    "bind-mount-long.yml": [f"service `web`: bind mounts are not allowed (`/var/run/docker.sock`); {BIND}"],
    "volume-undeclared.yml": ["service `web`: volume `data` is not declared under top-level volumes:"],
    "volume-driver-opts.yml": [
        "volume `data` must be declared with no options (no driver, driver_opts, external or name)"
    ],
    "volume-external.yml": [
        "volume `data` must be declared with no options (no driver, driver_opts, external or name)"
    ],
    "volume-name.yml": [
        "volume `data` must be declared with no options (no driver, driver_opts, external or name)"
    ],
    "network-external.yml": [
        "network `edge` may only set `internal:` (no driver, driver_opts, external, name or ipam)"
    ],
    "network-reserved.yml": ["network name `golem-apps` is reserved for the deployer"],
    "privileged.yml": ["service `web`: `privileged` is not allowed"],
    "cap-add.yml": ["service `web`: `cap_add` is not allowed"],
    "devices.yml": ["service `web`: `devices` is not allowed"],
    "network-mode.yml": ["service `web`: `network_mode` is not allowed"],
    "pid.yml": ["service `web`: `pid` is not allowed"],
    "ipc.yml": ["service `web`: `ipc` is not allowed"],
    "userns-mode.yml": ["service `web`: `userns_mode` is not allowed"],
    "security-opt.yml": ["service `web`: `security_opt` is not allowed"],
    "sysctls.yml": ["service `web`: `sysctls` is not allowed"],
    "ports.yml": [
        "service `web`: `ports` is not allowed (set golem.port on the public service; "
        "services reach each other over the project network)"
    ],
    "container-name.yml": ["service `web`: `container_name` is not allowed (the deployer names containers)"],
    "env-file.yml": ["service `web`: `env_file` is not allowed (list variables under environment:)"],
    "extends.yml": ["service `web`: `extends` is not allowed (put everything in deploy/compose.yml)"],
    "include.yml": ["`include` is not allowed (put everything in deploy/compose.yml)"],
    "secrets-file.yml": [
        "`secrets` is not allowed (use environment: or a config with inline content:)",
        "service `web`: `secrets` is not allowed (use environment: or a config with inline content:)",
    ],
    "configs-file.yml": [
        "config `conf` must have inline `content:` and nothing else "
        "(file, environment and external configs are not allowed)"
    ],
    "project-name.yml": ["`name` is not allowed (the deployer sets the project name)"],
    "traefik-label.yml": [
        "service `web`: label `traefik.http.routers.x.rule` is not allowed (the deployer writes all routing)"
    ],
    "traefik-label-list.yml": [
        "service `web`: label `Traefik.enable` is not allowed (the deployer writes all routing)"
    ],
    "golem-label.yml": [
        "service `web`: label `golem.slug` is not allowed (only golem.public and golem.port)"
    ],
    "memory-missing.yml": ["service `web`: a memory limit is required (for example mem_limit: 128m)"],
    "memory-over-service-cap.yml": ["service `web`: memory limit 1024 MB is over the per-service cap of 512 MB"],
    "memory-over-project-cap.yml": ["total memory limit 1200 MB is over the per-project cap of 1024 MB"],
    "public-missing.yml": ["exactly one service needs the label golem.public=true (none found)"],
    "public-twice.yml": ["exactly one service needs the label golem.public=true (web, admin)"],
    "public-no-port.yml": ["service `web`: the public service needs the label golem.port=<port>"],
    "port-on-private.yml": ["service `db`: label golem.port is only allowed on the public service"],
    "interpolation.yml": [
        "`$` interpolation is not allowed (at services.web.environment.TOKEN); write `$$` for a literal `$`"
    ],
    "duplicate-key.yml": ["invalid YAML: duplicate key `image`"],
    "yaml-tag.yml": ["invalid YAML: could not determine a constructor for the tag '!override'"],
    "anchors.yml": ["YAML anchors and aliases are not allowed"],
    "deploy-devices.yml": [
        "service `web`: `deploy.resources.reservations.devices` is not allowed "
        "(deploy: may only set resources.limits and resources.reservations)"
    ],
}


def read(name: str) -> str:
    return (FIXTURES / name).read_text()


def reasons_for(text: str, policy: Policy = Policy()) -> list[str]:
    with pytest.raises(Rejected) as excinfo:
        validate_compose(text, policy)
    return excinfo.value.reasons


def test_valid_fixture_passes():
    assert validate_compose(read("valid.yml")) == Project(("web", "db", "cache"), "web", 3000)


@pytest.mark.parametrize("name", sorted(REJECTIONS))
def test_bad_fixture_is_rejected_with_exact_reason(name):
    assert reasons_for(read(name)) == REJECTIONS[name]


def test_every_fixture_is_covered():
    on_disk = {p.name for p in FIXTURES.glob("*.yml")}
    assert on_disk == {"valid.yml", *REJECTIONS}


# --- Slugs and commits ----------------------------------------------------------


@pytest.mark.parametrize("slug", ["hello", "trees-quiz", "a1", "0day", "a" * 31])
def test_good_slugs(slug):
    assert check_slug(slug) == slug


@pytest.mark.parametrize(
    "slug",
    ["", "a", "Hello", "-hello", "hello-", "hello_world", "hello.dev", "a" * 32, "hello\n", None, 42],
)
def test_bad_slugs(slug):
    with pytest.raises(Rejected) as excinfo:
        check_slug(slug)
    assert excinfo.value.reasons == ["slug must match ^[a-z0-9][a-z0-9-]{1,30}$ and not end in `-`"]


@pytest.mark.parametrize("slug", ["golem", "projects", "www", "status", "traefik", "deployer", "api", "mail"])
def test_reserved_slugs(slug):
    with pytest.raises(Rejected) as excinfo:
        check_slug(slug)
    assert excinfo.value.reasons == [f"slug `{slug}` is reserved"]


def test_good_sha():
    sha = "0123456789abcdef0123456789abcdef01234567"
    assert check_sha(sha) == sha


@pytest.mark.parametrize("sha", ["main", "0123456", "0123456789ABCDEF0123456789ABCDEF01234567", "0" * 40 + "\n", None])
def test_bad_sha(sha):
    with pytest.raises(Rejected):
        check_sha(sha)


# --- Images ---------------------------------------------------------------------


def service(image: str) -> str:
    return f"""
services:
  web:
    image: "{image}"
    mem_limit: 64m
    labels: {{golem.public: "true", golem.port: "80"}}
"""


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/unlaunched/hello",
        "ghcr.io/unlaunched/hello:latest",
        "ghcr.io/unlaunched/trees/web:3f2a9c1",
        "ghcr.io/unlaunched/hello@sha256:" + "a" * 64,
        "postgres",
        "postgres:18-alpine",
        "library/nginx:1.29",
        "docker.io/redis:8",
        "docker.io/library/caddy:2",
    ],
)
def test_allowed_images(image):
    validate_compose(service(image))


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/unlaunched-evil/hello:1",  # look-alike org
        "ghcr.io/Unlaunched/hello:1",
        "ghcr.io/unlaunched/../evil/hello:1",
        "ghcr.io/unlaunched/",
        "ghcr.io/unlaunched/hello:1\n",
        "evil.io/ghcr.io/unlaunched/hello:1",
        "docker.io/evil/postgres:18",  # not the official one
        "quay.io/postgres:18",
        "postgresql:18",
        "traefik:v3",
    ],
)
def test_rejected_images(image):
    reasons = reasons_for(service(image))
    assert len(reasons) == 1 and "is not allowed; use ghcr.io/unlaunched/<name>" in reasons[0]


def test_org_comes_from_policy():
    validate_compose(service("ghcr.io/other/hello:1"), Policy(org="other"))


# --- Smaller rules --------------------------------------------------------------


def test_memory_caps_come_from_policy():
    text = service("ghcr.io/unlaunched/hello:1").replace("64m", "600m")
    validate_compose(text, Policy(service_mem_cap_mb=1024))


@pytest.mark.parametrize("size", ["128m", "128M", "128mb", "0.25g", "1048576", "512k"])
def test_memory_size_formats(size):
    validate_compose(service("ghcr.io/unlaunched/hello:1").replace("64m", size))


def test_invalid_memory_size():
    text = service("ghcr.io/unlaunched/hello:1").replace("64m", "lots")
    assert reasons_for(text) == ["service `web`: memory limit `lots` is not a valid size"]


def test_memory_set_twice():
    text = service("ghcr.io/unlaunched/hello:1") + "    deploy: {resources: {limits: {memory: 64m}}}\n"
    assert reasons_for(text) == [
        "service `web`: set the memory limit once, as mem_limit or deploy.resources.limits.memory"
    ]


def test_non_string_keys_are_rejected():
    text = service("ghcr.io/unlaunched/hello:1") + "    environment: {no: 1}\n"
    assert reasons_for(text) == ["keys must be strings (found `False`; quote it)"]


def test_file_size_cap():
    text = service("ghcr.io/unlaunched/hello:1") + "#" * 70_000
    assert reasons_for(text) == ["compose file is over 64 KB"]


@pytest.mark.parametrize("text", ["", "just a string", "- a\n- list\n"])
def test_not_a_mapping(text):
    assert reasons_for(text) == ["compose file must be a mapping"]


def test_no_services():
    assert reasons_for("volumes: {}\n") == ["`services` must be a non-empty mapping"]


HELLO = service("ghcr.io/unlaunched/hello:1")


@pytest.mark.parametrize(
    "extra, reason",
    [
        (
            "    restart: sometimes\n",
            "service `web`: restart must be one of always, no, on-failure, unless-stopped",
        ),
        (
            "    networks: [backend]\n",
            "service `web`: network `backend` is not declared under top-level networks:",
        ),
        (
            "    networks: {default: {ipv4_address: 10.0.0.5}}\n",
            "service `web`: network `default` may only set aliases",
        ),
        (
            "    volumes: [{type: volume, source: data, target: /d, volume: {subpath: x}}]\nvolumes: {data: }\n",
            "service `web`: long-form volume entries may only set type: volume, source, target and read_only",
        ),
        (
            "    volumes: ['data:/d:z']\nvolumes: {data: }\n",
            "service `web`: volume entry `data:/d:z` is invalid; use <volume>:<path>[:ro]",
        ),
        (
            "    configs: [nginx]\n",
            "service `web`: config `nginx` is not declared under top-level configs:",
        ),
    ],
)
def test_service_rules(extra, reason):
    assert reasons_for(HELLO + extra) == [reason]


@pytest.mark.parametrize("name", ["hello.golem", "Web", "-web", "a" * 64])
def test_service_names(name):
    text = HELLO.replace("  web:", f"  {name}:")
    assert reasons_for(text) == [f"service name `{name}` is invalid (lowercase letters, digits, `-` and `_`)"]


def test_docker_labels_are_reserved():
    text = HELLO.replace("labels: {", "labels: {com.docker.compose.project: someone-else,")
    assert reasons_for(text) == [
        "service `web`: label `com.docker.compose.project` is not allowed (reserved for Docker)"
    ]


def test_public_label_must_be_true():
    text = HELLO.replace('golem.public: "true"', "golem.public: maybe")
    assert reasons_for(text) == [
        "service `web`: label golem.public must be `true`",
        "service `web`: label golem.port is only allowed on the public service",
        "exactly one service needs the label golem.public=true (none found)",
    ]


@pytest.mark.parametrize("port", ["http", "0", "65536", "-1"])
def test_public_port_must_be_a_port(port):
    text = HELLO.replace('golem.port: "80"', f'golem.port: "{port}"')
    assert reasons_for(text) == ["service `web`: label golem.port must be a port number from 1 to 65535"]


def test_unquoted_public_label_is_accepted():
    text = HELLO.replace('golem.public: "true"', "golem.public: true").replace('"80"', "80")
    assert validate_compose(text) == Project(("web",), "web", 80)


def test_several_problems_are_reported_together():
    text = """
services:
  web:
    image: ghcr.io/unlaunched/hello:1
    privileged: true
    ports: ["80:80"]
    labels: {golem.public: "true", golem.port: "80"}
"""
    assert reasons_for(text) == [
        "service `web`: `privileged` is not allowed",
        "service `web`: `ports` is not allowed (set golem.port on the public service; "
        "services reach each other over the project network)",
        "service `web`: a memory limit is required (for example mem_limit: 128m)",
    ]


def test_golem_has_both_its_hosts():
    from deployer.compose import golem_route

    routers = golem_route("unlaunched.dev")["http"]["routers"]
    assert {name: r["rule"] for name, r in routers.items()} == {
        "golem": "Host(`golem.unlaunched.dev`)",
        "projects": "Host(`projects.unlaunched.dev`)",
    }
    assert {r["service"] for r in routers.values()} == {"golem"}
