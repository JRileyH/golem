"""Golem's built-in workflows: YAML block lists in `workflows/`, read into graphs.

Each `workflows/<key>.yml` is one workflow: its name, start type, ingest prompt, its markdown and
its blocks, wired in order: a block's first output goes to the next block unless it names
another; its other outputs name theirs by block id.

    key: change
    name: Change
    start: change
    ingest: He's asking for a feature, a fix or a change to be made.
    md: change.md                  # the intro, and a `## <name>` section per agent block
    blocks:
      - setup
      - {stage: build, name: change, turns: 400}   # a stage block: golem's text, set up (stages.py)
      - {stage: review, turns: 150, optional: true, turn: none}
      - {use: ship}                # a container: the `ship` sub-workflow
      - end

A block's id is its name (an agent's, a container's workflow…) unless it says `id:`; a name
used twice gets -2, -3.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import engine, stages, validate
from .store import Store, Workflow

SECTION_RE = re.compile(r"^## +(\S+) *$", re.M)
STARTS = ("new", "change", "look", "propose", "adopt", "sub")
# What each kind of entry in a block list is, by its key.
KINDS = ("agent", "do", "use", "checkin", "choose", "counter", "condition", "script", "output", "stage")
STAGE_FIELDS = ("turns", "optional", "turn", "model", "effort", "skip_to", "id", "name", "care", "extra", "about")
FIELDS = {  # what each block type may set, besides its kind key and wires
    "agent": ("turns", "optional", "turn", "contract", "outputs", "model", "effort", "skip_to", "id"),
    "do": ("id",),
    "use": ("name", "id"),
    "checkin": ("message", "reply_to", "optional", "turn", "outputs", "skip_to", "id"),
    "choose": ("optional", "skip_to", "id"),
    "counter": ("id",),
    "condition": ("all", "any", "id"),
    "script": ("command", "timeout", "optional", "turn", "skip_to", "id"),
    "output": ("id",),
}


@dataclass
class Builtin:
    """A workflow as golem ships it."""

    key: str
    name: str
    start: str
    ingest: str
    fallback: bool
    graph: dict = field(default_factory=dict)


_LOADED: dict[tuple, dict[str, Builtin]] = {}


def load_all(folder: Path) -> dict[str, Builtin]:
    """Every built-in workflow in the folder, by key, with its graphs. Read once per version of
    the files: treat what it returns as read-only."""
    stamp = (str(folder), *sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in folder.glob("*.*")))
    if stamp not in _LOADED:
        _LOADED.clear()
        _LOADED[stamp] = _load_all(folder)
    return _LOADED[stamp]


def _load_all(folder: Path) -> dict[str, Builtin]:
    specs = {}
    for path in sorted(folder.glob("*.yml")):
        spec = yaml.safe_load(path.read_text())
        if spec.get("key") != path.stem:
            raise ValueError(f"{path.name}: key must be {path.stem!r}")
        if spec.get("start") not in STARTS:
            raise ValueError(f"{path.name}: start is one of {', '.join(STARTS)}")
        specs[spec["key"]] = spec
    outputs: dict[str, list[str]] = {}
    for key, spec in specs.items():  # what each sub-workflow's containers' outputs are
        if spec["start"] == "sub":
            outputs[key] = [b["output"] for b in spec["blocks"] if isinstance(b, dict) and "output" in b]
    found = {}
    for key, spec in specs.items():
        md = (folder / spec["md"]).read_text() if spec.get("md") else ""
        try:
            graph = build(spec["blocks"], md, spec["start"] == "sub", outputs)
        except ValueError as e:
            raise ValueError(f"{key}.yml: {e}") from None
        names = {n["name"] for n in graph["nodes"] if n["type"] == "agent"}
        if extra := set(SECTION_RE.split(md)[1::2]) - names:
            raise ValueError(f"{spec['md']}: sections without an agent block: {', '.join(sorted(extra))}")
        found[key] = Builtin(key, spec["name"], spec["start"], (spec.get("ingest") or "").strip(), bool(spec.get("fallback")), graph)
    return found


def build(blocks: list, md: str, sub: bool, outputs: dict[str, list[str]]) -> dict:
    """A tab's graph from its block list and markdown."""
    parts = SECTION_RE.split(md)
    intro, sections = parts[0].strip(), dict(zip(parts[1::2], (s.strip() for s in parts[2::2])))
    nodes, wired, taken = [], [], set()
    for item in blocks:
        node, ports_named = _block(item, sections)
        base = node.pop("_id")
        nid, n = base, 1
        while nid in taken:
            n += 1
            nid = f"{base}-{n}"
        taken.add(nid)
        node["id"] = nid
        nodes.append(node)
        wired.append(ports_named)
    start = {"id": "input" if sub else "start", "type": "input" if sub else "start"}
    edges = [{"from": start["id"], "port": engine.NEXT, "to": nodes[0]["id"]}]
    for i, (node, named) in enumerate(zip(nodes, wired)):
        outs = engine.ports(node, {node["id"]: outputs.get(node.get("workflow", ""), [])})
        for p in named:
            if p not in outs:
                raise ValueError(f"{node['id']} has no {p} output (it has {', '.join(outs) or 'none'})")
        for j, p in enumerate(outs):
            if p in named:
                to = named[p]
            elif j == 0 and i + 1 < len(nodes):
                to = nodes[i + 1]["id"]
            else:
                raise ValueError(f"{node['id']}'s {p} output isn't wired")
            edges.append({"from": node["id"], "port": p, "to": to})
    graph = {"v": 1, "intro": intro, "nodes": [start, *nodes], "edges": edges}
    known = {n["id"] for n in graph["nodes"]}
    if bad := [e["to"] for e in edges if e["to"] not in known]:
        raise ValueError(f"wired to {bad[0]!r}, which isn't a block")
    return graph


def _block(item: str | dict, sections: dict[str, str]) -> tuple[dict, dict[str, str]]:
    """One entry of a block list: its node, and the outputs it wires by name."""
    if isinstance(item, str):
        if item == "end":
            return {"type": "end", "_id": "end"}, {}
        if item not in engine.CODE:
            raise ValueError(f"unknown block {item!r}")
        return {"type": item, "_id": item}, {}
    item = {("true" if k is True else "false" if k is False else k): v for k, v in item.items()}
    kinds = [k for k in KINDS if k in item]
    if len(kinds) != 1:
        raise ValueError(f"a block says what it is with one of {', '.join(KINDS)}: {item}")
    kind = kinds[0]
    value = item[kind]
    rest = {k: v for k, v in item.items() if k != kind}
    fields = (*STAGE_FIELDS, *stages.setting_keys()) if kind == "stage" else FIELDS[kind]
    settings = {k: rest.pop(k) for k in fields if k in rest}
    wires = {str(k): str(v) for k, v in rest.items()}  # anything else names an output
    if kind == "agent":
        if value not in sections:
            raise ValueError(f"agent block {value} has no '## {value}' section")
        outputs = [o if isinstance(o, dict) else {"name": o, "about": ""} for o in settings.pop("outputs", [])]
        node = {"type": "agent", "name": value, "instructions": sections[value], **settings}
        if outputs:
            node["outputs"] = outputs
        return {**node, "_id": settings.get("id", value)}, wires
    if kind == "stage":
        if value not in stages.catalog():
            raise ValueError(f"unknown stage {value!r}")
        node = stages.derive({"type": "agent", "stage": value, "name": settings.get("name", value), **settings})
        node.pop("id", None)
        return {**node, "_id": settings.get("id", node["name"])}, wires
    if kind == "do":
        if value not in engine.CODE:
            raise ValueError(f"unknown block {value!r}")
        return {"type": value, "_id": settings.get("id", value)}, wires
    if kind == "use":
        return {"type": "container", "workflow": value, **({"name": settings["name"]} if "name" in settings else {}),
                "_id": settings.get("id", value)}, wires  # fmt: skip
    if kind == "condition":
        join = "any" if "any" in settings else "all"
        rows = [{"of": of, "name": name, "op": op, "value": v} for of, name, op, v in settings.get(join) or []]
        return {"type": "condition", "name": value, "join": join, "rows": rows, "_id": settings.get("id", value)}, wires
    if kind == "counter":
        return {"type": "counter", "counter": value, "_id": settings.get("id", value)}, wires
    if kind == "output":
        return {"type": "output", "name": value, "_id": settings.get("id", f"out-{value}")}, wires
    settings.pop("id", None)
    if "outputs" in settings:
        settings["outputs"] = [o if isinstance(o, dict) else {"name": o, "about": ""} for o in settings["outputs"]]
    return {"type": kind, "name": value, **settings, "_id": item.get("id", value)}, wires


def resolver(found: dict[str, Builtin]) -> engine.Resolver:
    """Opens containers from the built-ins."""

    def resolve(key: str, tab: str, mode: str) -> engine.Sub:
        if key not in found:
            raise engine.GraphError(f"no workflow called {key}")
        return engine.Sub(key, found[key].name, "", found[key].graph)

    return resolve


def top(found: dict[str, Builtin], key: str, mode: str) -> engine.Sub:
    """A workflow at the top of a job."""
    return engine.Sub(key, found[key].name, "", found[key].graph)


def catalog(found: dict[str, Builtin], modes: tuple[str, ...] = ()):
    """What validation needs of the built-ins: each sub-workflow's Outputs."""
    subs = {key: validate.SubInfo(wf.name, tuple(engine.outputs_of(wf.graph)), ()) for key, wf in found.items() if wf.start == "sub"}
    return validate.Catalog(subs, resolver(found), modes)


# --- workflows in golem's database ----------------------------------------------------

FALLBACK = "fallback_"  # kv: the workflow (by key) each start type uses when nothing else fits


def digest(b: Builtin) -> str:
    return engine.digest({"name": b.name, "start": b.start, "ingest": b.ingest, "graph": b.graph})


class Library:
    """The workflows golem has, stored: what new jobs copy. Built-ins come from `workflows/` (seed)."""

    def __init__(self, store: Store, folder: Path, secret: str):
        self.store = store
        self.folder = folder
        self.key = hashlib.sha256(f"golem workflows:{secret}".encode()).digest()
        stages.use(folder / "stages")
        self.blocks = stages.Blocks(store, secret)  # the stage blocks' texts (Settings → Blocks)

    # --- signing ---------------------------------------------------------------

    def sign(self, graph: dict) -> str:
        """Golem's signature on a graph, keyed from a secret workers never get: a working graph
        changed outside the editor (by a worker that could reach the database) isn't run."""
        return hmac.new(self.key, json.dumps(graph, sort_keys=True).encode(), hashlib.sha256).hexdigest()

    def signed(self, tab) -> bool:
        return hmac.compare_digest(tab.sig, self.sign(tab.working))

    def resign_if_rekeyed(self) -> bool:
        """After the secret changed, every signature is golem's again (trusting what's stored then).
        Whether it had to."""
        check = hmac.new(self.key, b"check", hashlib.sha256).hexdigest()
        if self.store.get("workflow_key") == check:
            return False
        for wf in self.store.workflows():
            for mode, tab in self.store.tabs(wf.id).items():
                self.store.put_tab(wf.id, mode, tab.graph, tab.working, tab.problems, self.sign(tab.working))
        self.store.put("workflow_key", check)
        return True

    # --- golem's built-ins ------------------------------------------------------

    def seed(self) -> list[str]:
        """Golem's built-ins in the database: new ones added, unedited ones brought up to date,
        and an edited one told when golem has a newer version.
        Returns the keys it wrote."""
        self.resign_if_rekeyed()
        self.blocks.seed()
        found, wrote = load_all(self.folder), []
        for key, b in found.items():
            wf = self.store.workflow(key=key)
            if wf is None:
                handle = key if self.store.workflow(handle=key) is None else f"{key}_golem"
                wf = self.store.add_workflow(key, handle, b.name, b.start, b.ingest, builtin=True)
                self._write(wf, b)
                wrote.append(key)
            elif wf.seeded != digest(b):
                if wf.edited:
                    self.store.update_workflow(wf.id, upstream=digest(b))
                else:
                    self._write(wf, b)
                    wrote.append(key)
            if b.fallback and self.store.get(FALLBACK + b.start) is None:
                self.store.put(FALLBACK + b.start, key)
        return wrote

    def _write(self, wf: Workflow, b: Builtin) -> None:
        for mode in self.store.tabs(wf.id):
            if mode:
                self.store.drop_tab(wf.id, mode)
        self.store.put_tab(wf.id, "", b.graph, b.graph, [], self.sign(b.graph))
        self.store.update_workflow(wf.id, name=b.name, start=b.start, ingest=b.ingest, edited=False, seeded=digest(b), upstream="")

    # --- opening workflows -------------------------------------------------------

    def resolve(self, key: str, tab: str, mode: str) -> engine.Sub:
        """A container's sub-workflow: its tab (`default`, a mode's) or, with `auto`, the job's
        mode's if it has one, else its default. Its working graph: the latest with no problems."""
        wf = self.store.workflow(key=key)
        if wf is None:
            raise engine.GraphError(f"no workflow called {key}")
        want = mode if tab in ("auto", "") else ("" if tab == "default" else tab)
        return self._sub(wf, want)

    def top(self, wf: Workflow, mode: str) -> engine.Sub:
        """A workflow at the top of a job: its tab for the mode, else its default."""
        return self._sub(wf, mode)

    def _sub(self, wf: Workflow, want: str) -> engine.Sub:
        tab = self.store.tab(wf.id, want) or self.store.tab(wf.id, "")
        if tab is None:
            raise engine.GraphError(f"{wf.name} has no graph")
        if not self.signed(tab):
            raise engine.GraphError(f"{wf.name} was changed outside golem's editor, so golem won't run it")
        return engine.Sub(wf.key, wf.name, tab.mode, tab.working)

    def catalog(self) -> validate.Catalog:
        subs = {}
        for wf in self.store.workflows():
            if wf.start == "sub":
                tabs = self.store.tabs(wf.id)
                default = tabs.get("")
                subs[wf.key] = validate.SubInfo(
                    wf.name, tuple(engine.outputs_of(default.working if default else {})), tuple(m for m in tabs if m), wf.archived
                )
        return validate.Catalog(subs, self.resolve)

    def pin(self, wf: Workflow, start: str, mode: str) -> dict:
        """A job's own copy of the workflow, for its mode (engine.flatten). Raises GraphError if
        what's there now can't run."""
        root = self.top(wf, mode)
        report = validate.check(root.graph, start, self.catalog(), root.tab)
        if not report.ok:
            raise engine.GraphError(f"{wf.name} can't run: {report.errors[0].message}")
        return self.freeze(engine.flatten(root, start, mode, self.resolve))

    def freeze(self, snap: dict) -> dict:
        """Each stage block of a job's copy takes the texts it uses with it, as they are now, so
        changing them later never touches the job; and its instructions, put together without
        what it's about (known when it runs), for anyone looking at it."""
        for node in (snap.get("nodes") or {}).values():
            if node.get("type") == "agent" and node.get("stage"):
                node["parts"] = self.blocks.parts_for(node)
                node["instructions"] = stages.compose(node, node["parts"])
        return snap

    def fallback(self, start: str) -> Workflow | None:
        """The workflow a start type uses when nothing else fits."""
        key = self.store.get(FALLBACK + start)
        wf = self.store.workflow(key=key) if key else None
        return wf if wf and not wf.archived and wf.start == start else None

    def choices(self, starts: tuple[str, ...]) -> list[Workflow]:
        """The workflows a request could start, of these start types: not archived, and able to run."""
        found = []
        for wf in self.store.workflows(archived=False):
            if wf.start in starts and self.store.tab(wf.id, "") is not None:
                found.append(wf)
        return found
