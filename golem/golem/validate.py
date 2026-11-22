"""What makes a workflow one golem can run, and what's wrong with one that isn't.

Each problem names the block (and the output or field) it's about, so the editor can mark it.
Problems stop a workflow running: new jobs keep using its last working state until they're
fixed. Warnings are only shown.

Two kinds of rule. A graph's own: one Start, everything wired and reachable, every path
ending, agent blocks with instructions, and so on. And its start type's, checked with every
container opened (engine.flatten), so a sub-workflow is held to the rules of where it's used:
a job that changes a project starts by setting it up; one that only looks never pushes.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from . import engine

HUMAN = ("checkin", "choose")
MAKERS = ("options", "design")  # agent contracts a choose block shows the options of
TURNS_RANGE = (1, 1000)
SCRIPT_TIMEOUT = (1, 3600)  # seconds
# What a job of each start type can't do, besides its first block.
BANNED = {
    "new": ("refresh", "vet", "answer", "proposals"),
    "change": ("refresh", "vet", "answer", "proposals"),
    "adopt": ("refresh", "answer", "proposals"),
    "look": ("setup", "vet", *engine.SHIPS, "proposals"),
    "propose": ("setup", "vet", *engine.SHIPS, "answer", "checkin", "choose"),
}
STARTS_WITH = {"new": ("setup",), "change": ("setup",), "adopt": ("vet", "setup"), "look": ("refresh",), "propose": ("refresh",)}
WHAT = {"new": "a new project", "change": "a change", "adopt": "an adoption", "look": "a question", "propose": "proposals"}


@dataclass(frozen=True)
class Problem:
    message: str
    node: str = ""  # the block, at the graph's own level ("" for the whole workflow)
    port: str = ""
    field: str = ""

    def as_dict(self) -> dict:
        return {"node": self.node, "port": self.port, "field": self.field, "message": self.message}


@dataclass
class Report:
    errors: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str, node: str = "", port: str = "", field: str = "") -> None:
        self.errors.append(Problem(message, node, port, field))

    def warn(self, message: str, node: str = "", port: str = "", field: str = "") -> None:
        self.warnings.append(Problem(message, node, port, field))


@dataclass(frozen=True)
class SubInfo:
    """What a container needs to know of a sub-workflow."""

    name: str
    outputs: tuple[str, ...]
    tabs: tuple[str, ...]  # its mode tabs besides the default
    archived: bool = False


@dataclass
class Catalog:
    """The workflows a graph can contain, and how to open them (engine.Resolver)."""

    subs: dict[str, SubInfo]
    resolve: engine.Resolver
    modes: tuple[str, ...] = ()


def check(graph: dict, start: str, catalog: Catalog, mode: str = "") -> Report:
    """Everything wrong with one tab of a workflow of this start type."""
    report = Report()
    _structure(graph, start, catalog, report)
    if report.ok and start != "sub":
        _start_rules(graph, start, catalog, mode, report)
    return report


def check_tabs(tabs: dict[str, dict], start: str) -> Report:
    """Rules across a workflow's tabs: every tab of a sub-workflow has the same Outputs, since
    the containers that use it are wired by them whichever tab they open."""
    report = Report()
    if start == "sub" and tabs:
        default = engine.outputs_of(tabs.get("") or next(iter(tabs.values())))
        for mode, graph in tabs.items():
            if mode and engine.outputs_of(graph) != default:
                report.error(f"Every tab of a sub-workflow has the same Outputs ({', '.join(default) or 'none'}); the {mode} tab's differ")
    return report


# --- a graph's own rules ------------------------------------------------------------


def _structure(graph: dict, start: str, catalog: Catalog, report: Report) -> None:
    nodes = graph.get("nodes") or []
    ids = [n.get("id") for n in nodes]
    for nid in {i for i in ids if ids.count(i) > 1}:
        report.error("Two blocks have the same id", nid)
    by = {n["id"]: n for n in nodes}
    sub = start == "sub"
    for n in nodes:
        if n.get("type") not in engine.TYPES:
            report.error(f"Unknown kind of block: {n.get('type')!r}", n["id"])
    if not report.ok:
        return
    kinds = [n["type"] for n in nodes]
    begin, finish = ("input", "output") if sub else ("start", "end")
    if kinds.count(begin) != 1:
        report.error(f"A {'sub-workflow' if sub else 'workflow'} has exactly one {begin.title()} block")
    if finish not in kinds:
        report.error(f"A {'sub-workflow' if sub else 'workflow'} needs at least one {finish.title()} block: every path has to end")
    for n in nodes:
        if n["type"] in (("start", "end") if sub else ("input", "output")):
            report.error(f"{n['type'].title()} blocks are for {'workflows' if sub else 'sub-workflows'}: use {'Input and Output' if sub else 'Start and End'} here", n["id"])
    outputs = {}
    for n in nodes:
        if n["type"] == "container":
            info = catalog.subs.get(n.get("workflow", ""))
            outputs[n["id"]] = list(info.outputs) if info else []
    _blocks(nodes, by, catalog, report)
    wired: dict[str, dict[str, str]] = {}
    for e in graph.get("edges") or []:
        src, port, to = e.get("from"), e.get("port"), e.get("to")
        if src not in by:
            report.error("A wire comes from a block that isn't there")
            continue
        if port not in engine.ports(by[src], outputs):
            report.error(f"{_name(by[src])} has no {port} output any more: rewire it", src, port)
            continue
        if port in wired.get(src, {}):
            report.error(f"{_name(by[src])}'s {port} output is wired twice", src, port)
            continue
        if to not in by:
            report.error(f"{_name(by[src])}'s {port} output goes to a block that isn't there", src, port)
            continue
        if by[to]["type"] in ("start", "input"):
            report.error(f"Nothing can be wired into {by[to]['type'].title()}", src, port)
            continue
        wired.setdefault(src, {})[port] = to
    for n in nodes:
        for p in engine.ports(n, outputs):
            if p not in wired.get(n["id"], {}):
                report.error(f"{_name(n)}'s {p} output isn't wired to anything", n["id"], p)
    if not report.ok:
        return
    first = next(n["id"] for n in nodes if n["type"] == begin)
    reach = _reachable(first, wired)
    for n in nodes:
        if n["id"] not in reach:
            report.error(f"Nothing leads to {_name(n)}", n["id"])
    ends = {n["id"] for n in nodes if n["type"] == finish}
    back = _reverse(wired)
    can_end = set()
    for e in ends:
        can_end |= _reachable(e, back)
    for n in nodes:
        if n["id"] in reach and n["id"] not in can_end:
            report.error(f"From {_name(n)} there's no way to {finish.title()}: it would go round forever", n["id"])
    _routing(nodes, by, wired, back, report)
    _loops(nodes, by, wired, report)


def _blocks(nodes: list[dict], by: dict[str, dict], catalog: Catalog, report: Report) -> None:
    """Each block's own settings."""
    names: dict[str, str] = {}
    for n in nodes:
        t, nid = n["type"], n["id"]
        if n.get("optional") and t not in engine.SKIPPABLE:
            report.error(f"Only agent, script, check-in and choose blocks can be optional, not {t}", nid, field="optional")
        if n.get("skip_to") and n["skip_to"] not in engine.ports(n, {nid: []}):
            report.error(f"{_name(n)} has no {n['skip_to']} output to go to when skipped", nid, field="skip_to")
        if n.get("turn") and n["turn"] not in engine.TURNS:
            report.error(f"Turn is one of {', '.join(engine.TURNS)}", nid, field="turn")
        if t == "agent":
            name = (n.get("name") or "").strip()
            if not name:
                report.error("An agent block needs a name", nid, field="name")
            elif name in names:
                report.error(f"Two agent blocks are called {name}", nid, field="name")
            names[name] = nid
            if not (n.get("instructions") or "").strip():
                report.error(f"{_name(n)} needs instructions: what the agent should do", nid, field="instructions")
            turns = n.get("turns")
            if not isinstance(turns, int) or not TURNS_RANGE[0] <= turns <= TURNS_RANGE[1]:
                report.error(f"Turns is a whole number from {TURNS_RANGE[0]} to {TURNS_RANGE[1]}", nid, field="turns")
            contract = n.get("contract") or ""
            if contract and contract not in engine.CONTRACTS:
                report.error(f"Unknown contract {contract!r}", nid, field="contract")
            _outputs(n, report, needs_about=not contract)
        elif t == "checkin":
            _outputs(n, report, needs_about=True)
        elif t == "script":
            if not (n.get("command") or "").strip():
                report.error("A script block needs a command", nid, field="command")
            timeout = n.get("timeout", 600)
            if not isinstance(timeout, int) or not SCRIPT_TIMEOUT[0] <= timeout <= SCRIPT_TIMEOUT[1]:
                report.error(f"Timeout is a number of seconds from {SCRIPT_TIMEOUT[0]} to {SCRIPT_TIMEOUT[1]}", nid, field="timeout")
        elif t == "counter":
            if not (n.get("counter") or "").strip():
                report.error("A counter block needs the name of what it counts", nid, field="counter")
        elif t == "condition":
            for i, row in enumerate(n.get("rows") or []):
                if row.get("of") not in ("counter", "visits", "mode"):
                    report.error("A condition looks at a counter, a block's visits or the mode", nid, field=f"rows.{i}")
                elif row.get("of") == "mode":
                    if row.get("op") not in ("==", "!=", "is", "is not"):
                        report.error("The mode is or isn't something", nid, field=f"rows.{i}")
                elif row.get("op") not in engine.OPERATORS:
                    report.error(f"Compare with one of {' '.join(engine.OPERATORS)}", nid, field=f"rows.{i}")
                elif not str(row.get("value", "")).lstrip("-").isdigit():
                    report.error("Compare a count with a whole number", nid, field=f"rows.{i}")
                if row.get("of") == "visits" and row.get("name") not in by:
                    report.error(f"No block called {row.get('name')!r} to count the visits of", nid, field=f"rows.{i}")
            if n.get("join", "all") not in ("all", "any"):
                report.error("A condition needs all or any of its rows", nid, field="join")
        elif t == "output":
            if not (n.get("name") or "").strip():
                report.error("An Output block needs a name: it's what the container's output is called", nid, field="name")
        elif t == "container":
            info = catalog.subs.get(n.get("workflow", ""))
            if info is None:
                report.error("This container's sub-workflow isn't there any more", nid, field="workflow")
            else:
                if info.archived:
                    report.error(f"{info.name} is archived", nid, field="workflow")
                tab = n.get("tab") or "auto"
                if tab not in ("auto", "default", *info.tabs):
                    report.error(f"{info.name} has no {tab} tab", nid, field="tab")
    outs = [n["name"] for n in nodes if n["type"] == "output"]
    for name in {o for o in outs if outs.count(o) > 1}:
        report.error(f"Two Outputs are called {name}")


def _outputs(n: dict, report: Report, needs_about: bool) -> None:
    outs = n.get("outputs") or []
    names = [(o.get("name") or "").strip() for o in outs]
    for i, (o, name) in enumerate(zip(outs, names)):
        if not name:
            report.error("Every output needs a name", n["id"], field=f"outputs.{i}")
        elif name in engine.RESERVED:
            report.error(f"{name} is golem's own (the agent asks Riggy and carries on): call the output something else", n["id"], field=f"outputs.{i}")
        elif names.count(name) > 1:
            report.error(f"Two outputs are called {name}", n["id"], field=f"outputs.{i}")
        elif needs_about and len(outs) > 1 and not (o.get("about") or "").strip():
            report.error(f"Say when to take {name}: with several outputs, that's how the choice is made", n["id"], field=f"outputs.{i}")


def _routing(nodes: list[dict], by: dict[str, dict], wired: dict, back: dict, report: Report) -> None:
    """Blocks that look elsewhere in the graph: a pick needs options before it, a check-in's
    reply goes to an agent after it, and a condition's counters are counted somewhere."""
    counted = {n["counter"] for n in nodes if n["type"] == "counter"} | {"gaps"}  # vet counts gaps
    for n in nodes:
        if n["type"] == "choose":
            makers = {m["id"] for m in nodes if m["type"] == "agent" and m.get("contract") in MAKERS}
            if not makers & _reachable(n["id"], back):
                report.error("A choose block needs an options or design block before it: that's what it shows", n["id"])
            elif by.get(wired.get(n["id"], {}).get("more"), {}).get("contract") not in MAKERS:
                report.warn("Asking for other options usually goes back to the block that made them", n["id"], "more")
        if n["type"] == "checkin" and n.get("reply_to"):
            to = by.get(n["reply_to"])
            if to is None or to["type"] != "agent":
                report.error("The reply goes to an agent block", n["id"], field="reply_to")
            elif to["id"] not in _reachable(n["id"], wired) - {n["id"]}:
                report.error(f"The reply goes to an agent block after this one, and {_name(to)} doesn't come after it", n["id"], field="reply_to")
        if n["type"] == "condition":
            for i, row in enumerate(n.get("rows") or []):
                if row.get("of") == "counter" and row.get("name") not in counted:
                    report.warn(f"Nothing counts {row.get('name')}: it's always 0", n["id"], field=f"rows.{i}")


def _loops(nodes: list[dict], by: dict[str, dict], wired: dict, report: Report) -> None:
    """A loop of only counters and conditions would never stop; one with nothing to stop it
    (no condition, no asking Riggy) runs until golem's loop guard does."""
    control = {n["id"] for n in nodes if n["type"] in engine.CONTROL}
    only = {k: {p: t for p, t in v.items() if t in control} for k, v in wired.items() if k in control}
    for cycle in _cycles(only):
        report.error("Counters and conditions wired in a circle with nothing between them would never stop", cycle[0])
    stoppers = {n["id"] for n in nodes if n["type"] in ("condition", *HUMAN)}
    for cycle in _cycles(wired):
        if not set(cycle) & stoppers:
            report.warn("This loop has no condition or check-in to stop it: golem's loop guard will", cycle[0])


# --- the start type's rules, with containers opened ---------------------------------


def _start_rules(graph: dict, start: str, catalog: Catalog, mode: str, report: Report) -> None:
    try:
        snap = engine.flatten(engine.Sub("", "", mode, graph), start, mode, catalog.resolve)
    except engine.GraphError as e:
        report.error(str(e))
        return
    nodes, edges = snap["nodes"], snap["edges"]

    def at(nid: str) -> str:  # the block at the workflow's own level a flat block is in
        return nid.split("/", 1)[0]

    what = WHAT[start]
    first = STARTS_WITH[start]
    walk, nid = [], snap["start"]
    for _ in first:
        walk.append(nodes[nid]["type"])
        outs = engine.ports(nodes[nid])
        nid = edges[nid][outs[0]] if outs else None
        if nid is None:
            break
    if tuple(walk) != first:
        report.error(f"A workflow for {what} starts with {', then '.join(first)}", at(snap["start"]))
    for nid, n in nodes.items():
        if n["type"] in BANNED[start]:
            report.error(f"A workflow for {what} can't have a {n['type']} block", at(nid))
        if n["type"] in first and nid not in _first_n(snap, len(first)):
            report.error(f"{n['type']} only comes first", at(nid))
    if start in ("look", "propose"):
        last = "answer" if start == "look" else "proposals"
        without = {k: {p: t for p, t in v.items() if nodes[t]["type"] != last} for k, v in edges.items() if nodes[k]["type"] != last}
        if any(nodes[r]["type"] == "end" for r in _reachable(snap["start"], without)):
            report.error(f"Every way through a workflow for {what} passes {last}: that's what tells Riggy")
    if start == "propose":
        proposing = [nid for nid, n in nodes.items() if n["type"] == "agent" and n.get("contract") == "propose"]
        if len(proposing) != 1:
            report.error("A proposals workflow has exactly one agent block with the propose contract")


def _first_n(snap: dict, n: int) -> list[str]:
    found, nid = [], snap["start"]
    for _ in range(n):
        found.append(nid)
        outs = engine.ports(snap["nodes"][nid])
        if not outs:
            break
        nid = snap["edges"][nid][outs[0]]
    return found


# --- graph helpers ---------------------------------------------------------------


def _name(n: dict) -> str:
    return n.get("name") or n.get("counter") or n["type"]


def _reachable(start: str, wired: dict[str, dict[str, str]]) -> set[str]:
    seen, todo = {start}, deque([start])
    while todo:
        for to in wired.get(todo.popleft(), {}).values():
            if to not in seen:
                seen.add(to)
                todo.append(to)
    return seen


def _reverse(wired: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    back: dict[str, dict[str, str]] = {}
    for src, outs in wired.items():
        for p, to in outs.items():
            back.setdefault(to, {})[f"{src}:{p}"] = src
    return back


def _cycles(wired: dict[str, dict[str, str]]) -> list[list[str]]:
    """One cycle per strongly connected group of blocks that loops (Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    found: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in wired.get(v, {}).values():
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            group = []
            while True:
                w = stack.pop()
                on.discard(w)
                group.append(w)
                if w == v:
                    break
            if len(group) > 1 or v in wired.get(v, {}).values():
                found.append(sorted(group))

    nodes = set(wired) | {t for outs in wired.values() for t in outs.values()}
    for v in sorted(nodes):
        if v not in index:
            visit(v)
    return found
