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

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import engine, layout, validate
from .store import Store, Workflow

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
class Builtin:
    """A workflow as golem ships it."""

    key: str
    name: str
    start: str
    ingest: str
    fallback: bool
    tabs: dict[str, dict] = field(default_factory=dict)  # mode ("" for default) -> graph

    def graph(self, mode: str) -> dict:
        return self.tabs.get(mode) or self.tabs[""]


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
        found[key] = Builtin(key, spec["name"], spec["start"], (spec.get("ingest") or "").strip(), bool(spec.get("fallback")), tabs)
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


def resolver(found: dict[str, Builtin]) -> engine.Resolver:
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


def top(found: dict[str, Builtin], key: str, mode: str) -> engine.Sub:
    """A workflow at the top of a job: its tab for the mode, else its default."""
    wf = found[key]
    tab = mode if mode in wf.tabs else ""
    return engine.Sub(key, wf.name, tab, wf.tabs[tab])


def catalog(found: dict[str, Builtin], modes: tuple[str, ...] = ()):
    """What validation needs of the built-ins: each sub-workflow's Outputs and tabs."""
    subs = {
        key: validate.SubInfo(wf.name, tuple(engine.outputs_of(wf.tabs[""])), tuple(t for t in wf.tabs if t))
        for key, wf in found.items()
        if wf.start == "sub"
    }
    return validate.Catalog(subs, resolver(found), modes)


# --- workflows in golem's database ----------------------------------------------------

# The modes golem starts with, and what a request says to ask for one.
MODES = {
    "rigorous": 'He wants extra care taken: "be thorough", "take your time", "make it excellent", "rigorous mode".',
    "reckless": 'He wants it fast over careful: "quick and dirty", "just hack it together", "reckless mode".',
}
FALLBACK = "fallback_"  # kv: the workflow (by key) each start type uses when nothing else fits


def digest(b: Builtin) -> str:
    return engine.digest({"name": b.name, "start": b.start, "ingest": b.ingest, "tabs": b.tabs})


class Library:
    """The workflows golem has, stored: what new jobs copy, and what Riggy edits. Built-ins come
    from `workflows/` (seed); once he edits one, golem's later versions only say they're there."""

    def __init__(self, store: Store, folder: Path, secret: str):
        self.store = store
        self.folder = folder
        self.key = hashlib.sha256(f"golem workflows:{secret}".encode()).digest()

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
        and an edited one told when golem has a newer version. The modes golem starts with, once.
        Returns the keys it wrote."""
        self.resign_if_rekeyed()
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
        if self.store.get("modes_seeded") is None:
            for name, ingest in MODES.items():
                if self.store.mode(name) is None:
                    self.store.add_mode(name, ingest)
            self.store.put("modes_seeded", "1")
        return wrote

    def reset(self, wf: Workflow) -> Workflow:
        """A built-in back to golem's version."""
        b = load_all(self.folder)[wf.key]
        self._write(wf, b)
        return self.store.workflow(wf.id)

    def _write(self, wf: Workflow, b: Builtin) -> None:
        for mode in self.store.tabs(wf.id):
            if mode and mode not in b.tabs:
                self.store.drop_tab(wf.id, mode)
        for mode, graph in b.tabs.items():
            self.store.put_tab(wf.id, mode, graph, graph, [], self.sign(graph))
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
        return validate.Catalog(subs, self.resolve, tuple(m.name for m in self.store.modes()))

    def pin(self, wf: Workflow, start: str, mode: str) -> dict:
        """A job's own copy of the workflow, for its mode (engine.flatten). Raises GraphError if
        what's there now can't run."""
        root = self.top(wf, mode)
        report = validate.check(root.graph, start, self.catalog(), root.tab)
        if not report.ok:
            raise engine.GraphError(f"{wf.name} can't run: {report.errors[0].message}")
        return engine.flatten(root, start, mode, self.resolve)

    # --- editing ---------------------------------------------------------------

    def save(self, wf: Workflow, mode: str, graph: dict) -> validate.Report:
        """Save a tab as Riggy left it. Whatever's wrong with it is kept with it, to show on its
        blocks; new jobs copy its latest working state (which this becomes if nothing's wrong).
        Workflows that contain this one are checked again: a sub-workflow's outputs may have changed."""
        report = self.check(wf, mode, graph)
        tab = self.store.tab(wf.id, mode)
        working = graph if report.ok else (tab.working if tab else blank(wf.start))
        self.store.put_tab(wf.id, mode, graph, working, [p.as_dict() for p in report.errors], self.sign(working))
        self.store.update_workflow(wf.id, edited=True)
        for other, t in self.store.tabs(wf.id).items():  # rules across tabs: the others may be fine, or not, now
            if other != mode:
                again = self.check(wf, other, t.graph)
                kept = t.graph if again.ok else t.working
                self.store.put_tab(wf.id, other, t.graph, kept, [p.as_dict() for p in again.errors], self.sign(kept))
        self.recheck_containing(wf.key)
        return report


    def check(self, wf: Workflow, mode: str, graph: dict) -> validate.Report:
        report = validate.check(graph, wf.start, self.catalog(), mode)
        tabs = {m: t.graph for m, t in self.store.tabs(wf.id).items()} | {mode: graph}
        across = validate.check_tabs(tabs, wf.start)
        report.errors += across.errors
        return report


    def recheck_containing(self, key: str, depth: int = 0) -> None:
        """Check again every workflow with a container for `key`, and those containing them."""
        if depth > 8:
            return
        for other in self.store.workflows():
            for mode, tab in self.store.tabs(other.id).items():
                if any(n.get("type") == "container" and n.get("workflow") == key for n in tab.graph.get("nodes") or []):
                    report = self.check(other, mode, tab.graph)
                    working = tab.graph if report.ok else tab.working
                    self.store.put_tab(other.id, mode, tab.graph, working, [p.as_dict() for p in report.errors], self.sign(working))
                    if other.start == "sub":
                        self.recheck_containing(other.key, depth + 1)


    def create(self, name: str, start: str) -> Workflow:
        """A new workflow of Riggy's: a key that never changes, a short name (handle), a blank graph."""
        base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30] or "workflow"
        handle, n = base, 1
        while self.store.workflow(handle=handle) or self.store.workflow(key=handle):
            n += 1
            handle = f"{base}_{n}"
        wf = self.store.add_workflow(handle, handle, name.strip()[:60] or "Workflow", start)
        graph = blank(start)
        self.store.put_tab(wf.id, "", graph, graph, [], self.sign(graph))
        return wf


    def add_tab(self, wf: Workflow, mode: str, copy_of: str) -> None:
        """A tab for a mode, starting as a copy of another of the workflow's tabs."""
        source = self.store.tab(wf.id, copy_of) or self.store.tab(wf.id, "")
        self.store.put_tab(wf.id, mode, source.graph, source.working, source.problems, self.sign(source.working))
        self.store.update_workflow(wf.id, edited=True)

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


# --- editing (the editor, static/editor.js) ----------------------------------------------

ID_RE = re.compile(r"[A-Za-z0-9][\w-]{0,59}")
MAX_NODES = 300
TEXT_CAP = 20_000  # an agent's instructions, a check-in's message
FIELD_CAP = 2_000
# What each kind of block keeps, besides its id, type and position: anything else sent is dropped.
KEEPS = {
    "agent": ("name", "instructions", "turns", "model", "effort", "turn", "contract", "optional", "skip_to", "outputs"),
    "script": ("name", "command", "timeout", "turn", "optional", "skip_to"),
    "counter": ("counter",),
    "condition": ("name", "join", "rows"),
    "checkin": ("name", "message", "reply_to", "turn", "optional", "skip_to", "outputs"),
    "choose": ("name", "optional", "skip_to"),
    "container": ("workflow", "tab", "name"),
    "output": ("name",),
}


def clean(graph: object) -> dict:
    """A graph as the editor sent it, kept to what golem knows: its blocks' own settings only,
    strings and sizes capped. Raises ValueError for something that isn't a graph at all."""
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise ValueError("That isn't a workflow graph.")
    if len(graph["nodes"]) > MAX_NODES:
        raise ValueError(f"A workflow has at most {MAX_NODES} blocks.")
    nodes, seen = [], set()
    for raw in graph["nodes"]:
        if not isinstance(raw, dict) or raw.get("type") not in engine.TYPES:
            raise ValueError("A block golem doesn't know.")
        nid = str(raw.get("id", ""))
        if not ID_RE.fullmatch(nid) or nid in seen:
            raise ValueError(f"A block's id ({nid[:20]!r}) is missing, repeated or has odd characters.")
        seen.add(nid)
        node = {"id": nid, "type": raw["type"], "x": _number(raw.get("x")), "y": _number(raw.get("y"))}
        for key in KEEPS.get(raw["type"], ()):
            if key in raw and raw[key] not in (None, ""):
                node[key] = _value(key, raw[key])
        nodes.append(node)
    edges = []
    for e in graph["edges"]:
        if isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("from", "port", "to")):
            edges.append({"from": e["from"][:60], "port": e["port"][:60], "to": e["to"][:60]})
    return {"v": 1, "intro": str(graph.get("intro") or "")[:TEXT_CAP], "nodes": nodes, "edges": edges}


def _number(value: object) -> int:
    try:
        return max(-100_000, min(100_000, int(float(value))))
    except (TypeError, ValueError):
        return 0


def _value(key: str, value: object) -> object:
    if key in ("turns", "timeout"):
        return _number(value)
    if key == "optional":
        return bool(value)
    if key == "outputs":
        outs = [o for o in value if isinstance(o, dict)][:12] if isinstance(value, list) else []
        return [{"name": str(o.get("name", ""))[:40].strip(), "about": str(o.get("about", ""))[:FIELD_CAP]} for o in outs]
    if key == "rows":
        rows = [r for r in value if isinstance(r, dict)][:12] if isinstance(value, list) else []
        return [{"of": str(r.get("of", ""))[:20], "name": str(r.get("name", ""))[:60], "op": str(r.get("op", "=="))[:6],
                 "value": str(r.get("value", ""))[:40]} for r in rows]  # fmt: skip
    return str(value)[: TEXT_CAP if key in ("instructions", "message") else FIELD_CAP]


def blank(start: str) -> dict:
    """A new workflow's graph: Start wired to End (Input to an Output, for a sub-workflow)."""
    if start == "sub":
        nodes = [{"id": "input", "type": "input", "x": 0, "y": 0}, {"id": "out-done", "type": "output", "name": "done", "x": 0, "y": 220}]
        return {"v": 1, "intro": "", "nodes": nodes, "edges": [{"from": "input", "port": engine.NEXT, "to": "out-done"}]}
    nodes = [{"id": "start", "type": "start", "x": 0, "y": 0}, {"id": "end", "type": "end", "x": 0, "y": 220}]
    return {"v": 1, "intro": "", "nodes": nodes, "edges": [{"from": "start", "port": engine.NEXT, "to": "end"}]}


def editor_catalog() -> list[dict]:
    """The blocks the editor's palette offers, with the fields its inspector shows for each."""
    from .store import EFFORTS, MODELS

    turn = {"key": "turn", "label": "Turn it takes", "kind": "select", "options": ["", *engine.TURNS],
            "help": "Changes on one project take turns building and shipping; empty is the block's usual."}  # fmt: skip
    optional = {"key": "optional", "label": "Riggy can skip it", "kind": "checkbox"}
    skip_to = {"key": "skip_to", "label": "When skipped, leave by", "kind": "port"}
    outputs = {"key": "outputs", "label": "Outputs", "kind": "outputs",
               "help": "With several, the agent chooses one with its verdict: say when to take each."}  # fmt: skip
    return [
        {"type": "agent", "label": "Agent", "group": "Work", "help": "A headless Claude Code run that does one piece of work and commits it.",
         "fields": [{"key": "name", "label": "Name", "kind": "text"}, {"key": "instructions", "label": "Instructions", "kind": "textarea"},
                    {"key": "turns", "label": "Turns", "kind": "number", "min": 1, "max": 1000, "default": 150},
                    {"key": "model", "label": "Model", "kind": "select", "options": ["", *MODELS]},
                    {"key": "effort", "label": "Effort", "kind": "select", "options": ["", *EFFORTS]},
                    {"key": "contract", "label": "Returns", "kind": "select", "options": ["", *engine.CONTRACTS],
                     "help": "options or design: things for a choose block to show; propose: proposals."},
                    outputs, turn, optional, skip_to]},  # fmt: skip
        {"type": "script", "label": "Script", "group": "Work", "help": "A command run in the job's checkout: passes on exit 0.",
         "fields": [{"key": "name", "label": "Name", "kind": "text"}, {"key": "command", "label": "Command", "kind": "text"},
                    {"key": "timeout", "label": "Timeout (seconds)", "kind": "number", "min": 1, "max": 3600, "default": 600}, turn, optional, skip_to]},  # fmt: skip
        {"type": "checkin", "label": "Ask Riggy", "group": "Riggy", "help": "Posts a message and waits for his reply.",
         "fields": [{"key": "name", "label": "Name", "kind": "text"},
                    {"key": "message", "label": "Message", "kind": "textarea", "help": "Empty: try the live site. $last, $url, $failed, $failure and counters fill in."},
                    {"key": "reply_to", "label": "His reply goes to", "kind": "agent"}, outputs, turn, optional, skip_to]},  # fmt: skip
        {"type": "choose", "label": "Riggy picks", "group": "Riggy", "help": "Shows the options the block before made and waits for his pick.",
         "fields": [{"key": "name", "label": "Name", "kind": "text"}, optional, skip_to]},
        {"type": "counter", "label": "Counter", "group": "Logic", "help": "Adds one to a count, kept for the job.",
         "fields": [{"key": "counter", "label": "Counts", "kind": "text", "default": "tries"}]},
        {"type": "condition", "label": "Condition", "group": "Logic", "help": "True or false, from counts, visits or the mode.",
         "fields": [{"key": "name", "label": "Name", "kind": "text"}, {"key": "join", "label": "True when", "kind": "select", "options": ["all", "any"]},
                    {"key": "rows", "label": "Rows", "kind": "rows"}]},  # fmt: skip
        {"type": "container", "label": "Sub-workflow", "group": "Logic", "help": "A whole sub-workflow as one block.",
         "fields": [{"key": "workflow", "label": "Sub-workflow", "kind": "sub"}, {"key": "tab", "label": "Tab", "kind": "tab"},
                    {"key": "name", "label": "Name", "kind": "text"}]},  # fmt: skip
        *({"type": t, "label": (plan_label := {"push-main": "Push to main", "pr": "PR", "checks": "CI"}.get(t, t.capitalize())), "group": "Golem",
           "help": f"Golem's own: {plan_label.lower()}.", "fields": []} for t in engine.CODE),  # fmt: skip
        {"type": "end", "label": "End", "group": "Ends", "help": "Tells Riggy it's done.", "fields": []},
        {"type": "output", "label": "Output", "group": "Ends", "help": "One way out of a sub-workflow: the container's output.",
         "fields": [{"key": "name", "label": "Name", "kind": "text", "default": "done"}]},
    ]
