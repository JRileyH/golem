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

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import engine, modes, stages
from .store import Store

SECTION_RE = re.compile(r"^## +(\S+) *$", re.M)
STARTS = ("new", "change", "look", "propose", "adopt", "sub")
# What each kind of entry in a block list is, by its key.
KINDS = ("agent", "do", "use", "checkin", "choose", "counter", "condition", "output", "stage")
STAGE_FIELDS = ("turns", "optional", "turn", "model", "effort", "skip_to", "id", "name", "care", "extra", "about")
FIELDS = {  # what each block type may set, besides its kind key and wires
    "agent": ("turns", "optional", "turn", "contract", "outputs", "model", "effort", "skip_to", "id"),
    "do": ("id",),
    "use": ("name", "id"),
    "checkin": ("message", "reply_to", "optional", "turn", "outputs", "skip_to", "id"),
    "choose": ("optional", "skip_to", "id"),
    "counter": ("id",),
    "condition": ("all", "any", "id"),
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

    def resolve(key: str) -> engine.Sub:
        if key not in found:
            raise engine.GraphError(f"no workflow called {key}")
        return engine.Sub(key, found[key].name, found[key].graph)

    return resolve


def top(found: dict[str, Builtin], key: str) -> engine.Sub:
    """A workflow at the top of a job."""
    return engine.Sub(key, found[key].name, found[key].graph)


def parts_for(node: dict, prompts: modes.Prompts) -> dict[str, str]:
    """The texts a stage block uses, in a prompt set (golem's own, by default)."""
    stage = stages.catalog().get(node.get("stage") or "")
    if stage is None:
        return {}
    names = stage.every_part(node)
    texts = prompts.stage_parts(stage.key, stages.golems_text().get(stage.key, {}))
    return {name: text for name, text in texts.items() if name in names and text}


class Library:
    """Golem's workflows as its image has them (`workflows/`, read-only): what new jobs copy. And
    the stage blocks' texts, which Riggy can change (Settings → Blocks)."""

    def __init__(self, store: Store, folder: Path, secret: str):
        self.folder = folder
        stages.use(folder / "stages")
        if (folder.parent / "modes").is_dir():  # golem's image has them beside its workflows (a test's copy may not)
            modes.use(folder.parent / "modes")

    @property
    def found(self) -> dict[str, Builtin]:
        return load_all(self.folder)

    def get(self, key: str) -> Builtin | None:
        return self.found.get(key)

    def all(self) -> list[Builtin]:
        return list(self.found.values())

    def fallback(self, start: str) -> Builtin | None:
        """The workflow a start type uses when nothing else fits (its file says `fallback: true`)."""
        return next((wf for wf in self.found.values() if wf.start == start and wf.fallback), None)

    def choices(self, starts: tuple[str, ...]) -> list[Builtin]:
        """The workflows a request could start, of these start types."""
        return [wf for wf in self.found.values() if wf.start in starts]

    def pin(self, wf: Builtin, start: str, mode: modes.Mode | None = None) -> dict:
        """A job's own copy of the workflow (engine.flatten), with its mode (modes.py): the
        workflow the mode runs in its place, if any, its stage blocks' texts, and the mode itself."""
        mode = mode or modes.chosen()
        wf = self.instead(wf, mode)
        return self.freeze(engine.flatten(top(self.found, wf.key), start, resolver(self.found)), mode)

    def instead(self, wf: Builtin, mode: modes.Mode) -> Builtin:
        """The workflow a mode runs in place of this one (of the same start type), else this one."""
        found = self.found.get(mode.workflows.get(wf.key, ""))
        return found if found is not None and found.start == wf.start else wf

    def freeze(self, snap: dict, mode: modes.Mode) -> dict:
        """A job's copy takes its mode with it, as it is now, so changing the mode later never
        touches the job: each stage block the texts it uses, golem's or its prompt set's (with
        its instructions put together without what it's about, known when it runs, for anyone
        looking), the prompt set's instructions after the workflow's own text, and the mode's
        record (modes.Mode.record) to run on and to say what it ran."""
        for node in (snap.get("nodes") or {}).values():
            if node.get("type") == "agent" and node.get("stage"):
                node["parts"] = parts_for(node, mode.prompts)
                node["instructions"] = stages.compose(node, node["parts"])
        if mode.prompts.instructions:
            intros = snap.setdefault("intros", {})
            intros[""] = f"{intros.get('', '')}\n\n{mode.prompts.instructions}".strip()
        snap["mode"] = mode.record()
        return snap
