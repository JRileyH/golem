"""Golem's built-in workflows: YAML block lists in `workflows/`, read into graphs.

Each `workflows/<key>.yml` is one workflow: its name, start type, ingest prompt and tabs (its
default and any for a mode). A tab is a list of blocks, wired in order: a block's first output
goes to the next block unless it names another; its other outputs name theirs by block id.

    key: change
    name: Change
    start: change
    ingest: He's asking for a feature, a fix or a change to be made.
    tabs:
      default:
        md: change.md          # the intro, and a `## <name>` section per agent block
        blocks:
          - setup
          - {agent: change, turns: 400}
          - {agent: review, turns: 150, optional: true, turn: none}
          - {use: ship, tab: default}     # a container: the `ship` sub-workflow
          - end

A block's id is its name (an agent's, a container's workflow…) unless it says `id:`; a name
used twice gets -2, -3. The markdown is a playbook's body: text every agent block gets, then
each agent block's instructions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import engine, layout

SECTION_RE = re.compile(r"^## +(\S+) *$", re.M)
STARTS = ("new", "change", "look", "propose", "adopt", "sub")
# What each kind of entry in a block list is, by its key.
KINDS = ("agent", "do", "use", "checkin", "choose", "counter", "condition", "script", "output")
FIELDS = {  # what each block type may set, besides its kind key and wires
    "agent": ("turns", "optional", "turn", "contract", "outputs", "model", "effort", "skip_to", "id"),
    "do": ("id",),
    "use": ("tab", "name", "id"),
    "checkin": ("message", "reply_to", "optional", "turn", "outputs", "skip_to", "id"),
    "choose": ("optional", "skip_to", "id"),
    "counter": ("id",),
    "condition": ("all", "any", "id"),
    "script": ("command", "timeout", "optional", "turn", "skip_to", "id"),
    "output": ("id",),
}


@dataclass
class Workflow:
    """A workflow as golem ships it."""

    key: str
    name: str
    start: str
    ingest: str
    fallback: bool
    tabs: dict[str, dict] = field(default_factory=dict)  # mode ("" for default) -> graph

    def graph(self, mode: str) -> dict:
        return self.tabs.get(mode) or self.tabs[""]


def load_all(folder: Path) -> dict[str, Workflow]:
    """Every built-in workflow in the folder, by key, with its graphs."""
    specs = {}
    for path in sorted(folder.glob("*.yml")):
        spec = yaml.safe_load(path.read_text())
        if spec.get("key") != path.stem:
            raise ValueError(f"{path.name}: key must be {path.stem!r}")
        if spec.get("start") not in STARTS:
            raise ValueError(f"{path.name}: start is one of {', '.join(STARTS)}")
        specs[spec["key"]] = spec
    outputs: dict[str, list[str]] = {}
    for key, spec in specs.items():  # what each sub-workflow's containers' outputs are, from its default tab
        if spec["start"] == "sub":
            default = spec["tabs"]["default"]["blocks"]
            outputs[key] = [b["output"] for b in default if isinstance(b, dict) and "output" in b]
    found = {}
    for key, spec in specs.items():
        tabs, used = {}, {}
        for tab, body in spec["tabs"].items():
            md = (folder / body["md"]).read_text() if body.get("md") else ""
            try:
                tabs["" if tab == "default" else tab] = graph = build(body["blocks"], md, spec["start"] == "sub", outputs)
            except ValueError as e:
                raise ValueError(f"{key}.yml, tab {tab}: {e}") from None
            names = used.setdefault(body.get("md", ""), set())
            names |= {n["name"] for n in graph["nodes"] if n["type"] == "agent"}
        for md, names in used.items():  # a tab can share another's markdown, but every section is some tab's
            parts = SECTION_RE.split((folder / md).read_text()) if md else [""]
            if extra := set(parts[1::2]) - names:
                raise ValueError(f"{md}: sections without an agent block: {', '.join(sorted(extra))}")
        found[key] = Workflow(key, spec["name"], spec["start"], (spec.get("ingest") or "").strip(), bool(spec.get("fallback")), tabs)
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
    layout.place(graph)
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
    settings = {k: rest.pop(k) for k in FIELDS[kind] if k in rest}
    wires = {str(k): str(v) for k, v in rest.items()}  # anything else names an output
    if kind == "agent":
        if value not in sections:
            raise ValueError(f"agent block {value} has no '## {value}' section")
        outputs = [o if isinstance(o, dict) else {"name": o, "about": ""} for o in settings.pop("outputs", [])]
        node = {"type": "agent", "name": value, "instructions": sections[value], **settings}
        if outputs:
            node["outputs"] = outputs
        return {**node, "_id": settings.get("id", value)}, wires
    if kind == "do":
        if value not in engine.CODE:
            raise ValueError(f"unknown block {value!r}")
        return {"type": value, "_id": settings.get("id", value)}, wires
    if kind == "use":
        return {"type": "container", "workflow": value, "tab": settings.get("tab", "auto"),
                **({"name": settings["name"]} if "name" in settings else {}), "_id": settings.get("id", value)}, wires  # fmt: skip
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


def resolver(found: dict[str, Workflow]) -> engine.Resolver:
    """Opens containers from the built-ins: a container's tab, or with `auto` the job's mode's
    tab if the sub-workflow has one, else its default."""

    def resolve(key: str, tab: str, mode: str) -> engine.Sub:
        if key not in found:
            raise engine.GraphError(f"no workflow called {key}")
        wf = found[key]
        want = mode if tab in ("auto", "") else ("" if tab == "default" else tab)
        have = want if want in wf.tabs else ""
        return engine.Sub(key, wf.name, have, wf.tabs[have])

    return resolve


def top(found: dict[str, Workflow], key: str, mode: str) -> engine.Sub:
    """A workflow at the top of a job: its tab for the mode, else its default."""
    wf = found[key]
    tab = mode if mode in wf.tabs else ""
    return engine.Sub(key, wf.name, tab, wf.tabs[tab])


def catalog(found: dict[str, Workflow], modes: tuple[str, ...] = ()):
    """What validation needs of the built-ins: each sub-workflow's Outputs and tabs."""
    from .validate import Catalog, SubInfo

    subs = {
        key: SubInfo(wf.name, tuple(engine.outputs_of(wf.tabs[""])), tuple(t for t in wf.tabs if t))
        for key, wf in found.items()
        if wf.start == "sub"
    }
    return Catalog(subs, resolver(found), modes)
