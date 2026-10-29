"""Validate a project's deploy/compose.yml against the deployer's allow-list.

This is the security boundary between agents and Docker. Anything not
explicitly allowed here is rejected, with a reason an agent can act on.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

import yaml

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,30}")
RESERVED_SLUGS = frozenset(
    {"golem", "projects", "www", "status", "traefik", "deployer", "api", "mail"}
)
SHA_RE = re.compile(r"[0-9a-f]{40}")

MAX_COMPOSE_BYTES = 64 * 1024
MIB = 1024 * 1024

# Docker Hub official images agents may use directly. Everything else is built
# in the project's CI and pushed to ghcr.io/<org>/.
OFFICIAL_IMAGES = frozenset(
    {"caddy", "httpd", "mariadb", "memcached", "mongo", "mysql", "nginx", "postgres", "redis"}
)

TOP_LEVEL_KEYS = frozenset({"services", "volumes", "networks", "configs", "version"})
SERVICE_KEYS = frozenset(
    {
        "command",
        "configs",
        "depends_on",
        "deploy",
        "entrypoint",
        "environment",
        "expose",
        "healthcheck",
        "image",
        "init",
        "labels",
        "mem_limit",
        "networks",
        "read_only",
        "restart",
        "stop_grace_period",
        "stop_signal",
        "user",
        "volumes",
        "working_dir",
    }
)
# deploy: is a tree; only these branches are allowed.
DEPLOY_KEYS = {"resources": {"limits": {"memory", "cpus", "pids"}, "reservations": {"memory", "cpus"}}}
VOLUME_MOUNT_KEYS = frozenset({"type", "source", "target", "read_only"})
CONFIG_MOUNT_KEYS = frozenset({"source", "target", "mode"})
RESTART_VALUES = frozenset({"no", "always", "unless-stopped", "on-failure"})

# The deployer's override attaches the public service to this network.
APPS_NETWORK = "golem-apps"
PUBLIC_LABEL = "golem.public"
PORT_LABEL = "golem.port"

NAME_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*")
# No dots or capitals: a project's public service gets the alias `<slug>.golem`
# on the shared apps network, and no service or container name may collide with it.
SERVICE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)([bkmg]?)b?", re.IGNORECASE)
SIZE_UNITS = {"": 1, "b": 1, "k": 1024, "m": MIB, "g": 1024 * MIB}

_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_TAG_AND_DIGEST = r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(?:@sha256:[a-f0-9]{64})?"
OFFICIAL_IMAGE_RE = re.compile(rf"(?:docker\.io/)?(?:library/)?(?P<name>{_COMPONENT}){_TAG_AND_DIGEST}")

HINTS = {
    "build": "build the image in CI and push it to ghcr.io/{org}/",
    "ports": f"set {PORT_LABEL} on the public service; services reach each other over the project network",
    "env_file": "list variables under environment:",
    "container_name": "the deployer names containers",
    "include": "put everything in deploy/compose.yml",
    "extends": "put everything in deploy/compose.yml",
    "secrets": "use environment: or a config with inline content:",
    "name": "the deployer sets the project name",
}


class Rejected(Exception):
    """A request the deployer refuses. `reasons` are written for the agent."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass(frozen=True)
class Policy:
    org: str = "unlaunched"
    service_mem_cap_mb: int = 512
    project_mem_cap_mb: int = 1024


@dataclass(frozen=True)
class Project:
    services: tuple[str, ...]
    public_service: str
    public_port: int
    images: dict[str, str] = field(default_factory=dict, compare=False)


def check_slug(slug: object) -> str:
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug) or slug.endswith("-"):
        raise Rejected([f"slug must match ^{SLUG_RE.pattern}$ and not end in `-`"])
    if slug in RESERVED_SLUGS:
        raise Rejected([f"slug `{slug}` is reserved"])
    return slug


def check_sha(sha: object) -> str:
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise Rejected(["sha must be a full 40-character lowercase commit hash"])
    return sha


def validate_compose(text: str, policy: Policy = Policy()) -> Project:
    """Return the project's shape if `text` passes every rule, else raise Rejected."""
    if len(text.encode()) > MAX_COMPOSE_BYTES:
        raise Rejected([f"compose file is over {MAX_COMPOSE_BYTES // 1024} KB"])
    doc = _load(text)
    validator = _Validator(policy)
    project = validator.run(doc)
    if validator.reasons or project is None:
        raise Rejected(validator.reasons)
    return project


# --- YAML loading -------------------------------------------------------------


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys, so we and Compose see the same file."""


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key `{key}`", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _load(text: str) -> dict:
    try:
        # Anchors and aliases are refused outright: they're rare in a single
        # compose file and can expand into huge documents.
        for event in yaml.parse(text, Loader=_StrictLoader):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise Rejected(["YAML anchors and aliases are not allowed"])
        doc = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as e:
        problem = getattr(e, "problem", None) or str(e).splitlines()[0]
        raise Rejected([f"invalid YAML: {problem}"]) from None
    except (TypeError, ValueError):
        raise Rejected(["invalid YAML: keys must be plain strings"]) from None
    if not isinstance(doc, dict):
        raise Rejected(["compose file must be a mapping"])
    if bad := _non_string_keys(doc):
        raise Rejected([f"keys must be strings (found `{bad[0]}`; quote it)"])
    return doc


def _non_string_keys(node: object) -> list:
    """Keys that aren't strings (YAML turns `on:`, `no:` and `1:` into bool/int)."""
    if isinstance(node, dict):
        bad = [key for key in node if not isinstance(key, str)]
        return bad + [k for value in node.values() for k in _non_string_keys(value)]
    if isinstance(node, list):
        return [k for value in node for k in _non_string_keys(value)]
    return []


# --- Validation ---------------------------------------------------------------


class _Validator:
    def __init__(self, policy: Policy):
        self.policy = policy
        self.reasons: list[str] = []
        org = re.escape(policy.org)
        self.org_image_re = re.compile(rf"ghcr\.io/{org}/{_COMPONENT}(?:/{_COMPONENT})*{_TAG_AND_DIGEST}")

    def reject(self, reason: str) -> None:
        self.reasons.append(reason)

    def not_allowed(self, where: str, key: str) -> None:
        reason = f"{where}`{key}` is not allowed"
        if hint := HINTS.get(key):
            reason += f" ({hint.format(org=self.policy.org)})"
        self.reject(reason)

    def run(self, doc: dict) -> Project | None:
        self.check_strings(doc)
        for key in doc:
            if key not in TOP_LEVEL_KEYS:
                self.not_allowed("", key)
        volumes = self.top_volumes(doc.get("volumes"))
        networks = self.top_networks(doc.get("networks"))
        configs = self.top_configs(doc.get("configs"))

        services = doc.get("services")
        if not isinstance(services, dict) or not services:
            self.reject("`services` must be a non-empty mapping")
            return None

        public, total_mem = [], 0
        for name, spec in services.items():
            if not SERVICE_NAME_RE.fullmatch(name):
                self.reject(f"service name `{name}` is invalid (lowercase letters, digits, `-` and `_`)")
            if not isinstance(spec, dict):
                self.reject(f"service `{name}` must be a mapping")
                continue
            service = _Service(self, name, spec)
            service.check(volumes, networks, configs)
            total_mem += service.memory or 0
            if service.is_public:
                public.append(service)

        if total_mem > self.policy.project_mem_cap_mb * MIB:
            self.reject(
                f"total memory limit {_mb(total_mem)} MB is over the per-project cap of "
                f"{self.policy.project_mem_cap_mb} MB"
            )
        if len(public) != 1:
            found = ", ".join(s.name for s in public) or "none found"
            self.reject(f"exactly one service needs the label {PUBLIC_LABEL}=true ({found})")
            return None
        if public[0].port is None:
            return None
        images = {name: spec["image"] for name, spec in services.items()}
        return Project(tuple(services), public[0].name, public[0].port, images)

    def check_strings(self, doc: dict) -> None:
        """No ${VAR} interpolation anywhere: it would read the deployer's own environment."""
        for path, value in _strings(doc, ""):
            if "$" in value.replace("$$", ""):
                self.reject(f"`$` interpolation is not allowed (at {path}); write `$$` for a literal `$`")

    def top_mapping(self, value: object, kind: str) -> dict:
        if value is None:
            return {}
        if not isinstance(value, dict):
            self.reject(f"top-level `{kind}` must be a mapping")
            return {}
        for name in value:
            if not NAME_RE.fullmatch(name):
                self.reject(f"{kind[:-1]} name `{name}` is invalid")
        return value

    def top_volumes(self, value: object) -> set[str]:
        volumes = self.top_mapping(value, "volumes")
        for name, spec in volumes.items():
            if spec not in (None, {}):
                self.reject(
                    f"volume `{name}` must be declared with no options "
                    "(no driver, driver_opts, external or name)"
                )
        return set(volumes)

    def top_networks(self, value: object) -> set[str]:
        networks = self.top_mapping(value, "networks")
        for name, spec in networks.items():
            if name == APPS_NETWORK:
                self.reject(f"network name `{APPS_NETWORK}` is reserved for the deployer")
            elif spec not in (None, {}) and not (
                isinstance(spec, dict) and spec.keys() == {"internal"} and isinstance(spec["internal"], bool)
            ):
                self.reject(
                    f"network `{name}` may only set `internal:` "
                    "(no driver, driver_opts, external, name or ipam)"
                )
        return set(networks)

    def top_configs(self, value: object) -> set[str]:
        configs = self.top_mapping(value, "configs")
        for name, spec in configs.items():
            if not (isinstance(spec, dict) and spec.keys() == {"content"} and isinstance(spec["content"], str)):
                self.reject(
                    f"config `{name}` must have inline `content:` and nothing else "
                    "(file, environment and external configs are not allowed)"
                )
        return set(configs)


class _Service:
    def __init__(self, validator: _Validator, name: str, spec: dict):
        self.v = validator
        self.name = name
        self.spec = spec
        self.where = f"service `{name}`: "
        self.memory: int | None = None
        self.is_public = False
        self.port: int | None = None

    def reject(self, reason: str) -> None:
        self.v.reject(self.where + reason)

    def check(self, volumes: set[str], networks: set[str], configs: set[str]) -> None:
        for key in self.spec:
            if key not in SERVICE_KEYS:
                self.v.not_allowed(self.where, key)
        self.check_image()
        self.check_labels()
        self.check_volumes(volumes)
        self.check_networks(networks)
        self.check_configs(configs)
        self.check_deploy()
        self.check_memory()
        restart = self.spec.get("restart")
        if restart is False:  # YAML 1.1 reads an unquoted `no` as false
            restart = "no"
        if restart is not None and restart not in RESTART_VALUES:
            self.reject(f"restart must be one of {', '.join(sorted(RESTART_VALUES))}")

    def check_image(self) -> None:
        image = self.spec.get("image")
        if image is None:
            self.reject("`image` is required")
            return
        if isinstance(image, str):
            if self.v.org_image_re.fullmatch(image):
                return
            official = OFFICIAL_IMAGE_RE.fullmatch(image)
            if official and official["name"] in OFFICIAL_IMAGES:
                return
        self.reject(
            f"image `{image}` is not allowed; use ghcr.io/{self.v.policy.org}/<name> "
            f"or an official image ({', '.join(sorted(OFFICIAL_IMAGES))})"
        )

    def check_labels(self) -> None:
        labels = _labels(self.spec.get("labels"))
        if labels is None:
            self.reject("labels must be a mapping or a list of key=value")
            return
        for key in labels:
            lowered = key.lower()
            if lowered.startswith("traefik."):
                self.reject(f"label `{key}` is not allowed (the deployer writes all routing)")
            elif lowered.startswith("com.docker."):
                self.reject(f"label `{key}` is not allowed (reserved for Docker)")
            elif lowered.startswith("golem.") and key not in (PUBLIC_LABEL, PORT_LABEL):
                self.reject(f"label `{key}` is not allowed (only {PUBLIC_LABEL} and {PORT_LABEL})")

        if PUBLIC_LABEL in labels:
            if labels[PUBLIC_LABEL] == "true":
                self.is_public = True
            else:
                self.reject(f"label {PUBLIC_LABEL} must be `true`")
        if PORT_LABEL in labels and not self.is_public:
            self.reject(f"label {PORT_LABEL} is only allowed on the public service")
        elif self.is_public:
            port = labels.get(PORT_LABEL)
            if port is None:
                self.reject(f"the public service needs the label {PORT_LABEL}=<port>")
            elif port.isdigit() and 1 <= int(port) <= 65535:
                self.port = int(port)
            else:
                self.reject(f"label {PORT_LABEL} must be a port number from 1 to 65535")

    def check_volumes(self, declared: set[str]) -> None:
        mounts = self.spec.get("volumes")
        if mounts is None:
            return
        if not isinstance(mounts, list):
            self.reject("volumes must be a list")
            return
        for mount in mounts:
            if isinstance(mount, str):
                self.check_short_mount(mount, declared)
            elif isinstance(mount, dict):
                self.check_long_mount(mount, declared)
            else:
                self.reject(f"volume entry `{mount}` is invalid")

    def check_short_mount(self, mount: str, declared: set[str]) -> None:
        parts = mount.split(":")
        if len(parts) == 1 and mount.startswith("/"):
            return  # anonymous volume at a container path
        if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2] not in ("ro", "rw")):
            self.reject(f"volume entry `{mount}` is invalid; use <volume>:<path>[:ro]")
            return
        self.check_mount_source(parts[0], mount, declared)

    def check_long_mount(self, mount: dict, declared: set[str]) -> None:
        source = mount.get("source")
        if mount.get("type") == "bind":
            self.reject(f"bind mounts are not allowed (`{source}`); use a named volume declared under top-level volumes:")
            return
        extra = set(mount) - VOLUME_MOUNT_KEYS
        if mount.get("type") != "volume" or extra:
            self.reject(
                "long-form volume entries may only set type: volume, source, target and read_only"
            )
            return
        self.check_mount_source(source, source, declared)

    def check_mount_source(self, source: object, shown: object, declared: set[str]) -> None:
        if isinstance(source, str) and source.startswith(("/", ".", "~")):
            self.reject(f"bind mounts are not allowed (`{shown}`); use a named volume declared under top-level volumes:")
        elif not isinstance(source, str) or source not in declared:
            self.reject(f"volume `{source}` is not declared under top-level volumes:")

    def check_networks(self, declared: set[str]) -> None:
        networks = self.spec.get("networks")
        if networks is None:
            return
        if isinstance(networks, dict):
            for name, opts in networks.items():
                if opts is not None and not (isinstance(opts, dict) and set(opts) <= {"aliases"}):
                    self.reject(f"network `{name}` may only set aliases")
            names = list(networks)
        elif isinstance(networks, list):
            names = networks
        else:
            self.reject("networks must be a list or mapping")
            return
        for name in names:
            if name != "default" and (not isinstance(name, str) or name not in declared):
                self.reject(f"network `{name}` is not declared under top-level networks:")

    def check_configs(self, declared: set[str]) -> None:
        configs = self.spec.get("configs")
        if configs is None:
            return
        if not isinstance(configs, list):
            self.reject("configs must be a list")
            return
        for entry in configs:
            if isinstance(entry, dict):
                if not set(entry) <= CONFIG_MOUNT_KEYS:
                    self.reject("config entries may only set source, target and mode")
                    continue
                source = entry.get("source")
            else:
                source = entry
            if not isinstance(source, str) or source not in declared:
                self.reject(f"config `{source}` is not declared under top-level configs:")

    def check_deploy(self) -> None:
        deploy = self.spec.get("deploy")
        if deploy is None:
            return
        for path in _tree_violations(deploy, DEPLOY_KEYS, "deploy"):
            self.reject(f"`{path}` is not allowed (deploy: may only set resources.limits and resources.reservations)")

    def check_memory(self) -> None:
        mem_limit = self.spec.get("mem_limit")
        deploy_limit = _dig(self.spec, "deploy", "resources", "limits", "memory")
        if mem_limit is not None and deploy_limit is not None:
            self.reject("set the memory limit once, as mem_limit or deploy.resources.limits.memory")
            return
        raw = mem_limit if mem_limit is not None else deploy_limit
        if raw is None:
            self.reject("a memory limit is required (for example mem_limit: 128m)")
            return
        size = _parse_size(raw)
        if size is None or size == 0:
            self.reject(f"memory limit `{raw}` is not a valid size")
            return
        self.memory = size
        if size > self.v.policy.service_mem_cap_mb * MIB:
            self.reject(
                f"memory limit {_mb(size)} MB is over the per-service cap of "
                f"{self.v.policy.service_mem_cap_mb} MB"
            )


# --- Helpers ------------------------------------------------------------------


def _strings(node: object, path: str) -> Iterator[tuple[str, str]]:
    """Every string in the document, keys included, with a dotted path to it."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _strings(key, child)
            yield from _strings(value, child)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _strings(value, f"{path}[{i}]")


def _labels(value: object) -> dict[str, str] | None:
    """Normalise labels to {key: value} as Compose would, or None if malformed."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return {k: _label_value(v) for k, v in value.items()}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return dict(item.split("=", 1) if "=" in item else (item, "") for item in value)
    return None


def _label_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _tree_violations(node: object, allowed: dict | set, path: str) -> Iterator[str]:
    """Paths in `node` that fall outside the `allowed` key tree."""
    if not isinstance(node, dict):
        yield path
        return
    for key, value in node.items():
        child = f"{path}.{key}"
        if key not in allowed:
            yield child
        elif isinstance(allowed, dict):
            yield from _tree_violations(value, allowed[key], child)


def _dig(node: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _parse_size(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    match = SIZE_RE.fullmatch(value.strip())
    if not match:
        return None
    return int(float(match[1]) * SIZE_UNITS[match[2].lower()])


def _mb(size: int) -> str:
    return f"{size / MIB:g}"
