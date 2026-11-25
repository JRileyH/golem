"""Workflows as graphs: the blocks, the wires between them, and how a job walks them.

A workflow is a graph of blocks. Every block has one way in and some named outputs, each
wired to exactly one block; the first output is the usual way on. Agent blocks are headless
Claude Code runs that pick which of their outputs to take with their verdict; golem's own
blocks (push, CI, deploy, check-ins…) take theirs by what happened; counters and conditions
are bookkeeping golem does on the way past. A container block is a whole sub-workflow,
wired by the sub-workflow's named Output blocks, and those can hold containers of their own.

When a job starts, its workflow is flattened: containers are opened up in place, so the job
walks one flat graph whose block ids are paths ("ship-2/fix"). That copy is the job's own and
nothing done to the workflow afterwards changes it. Each visit to a block is a row in the
job's steps (store.Step, with `node_id`, `visit` and the `port` it left by): the job's trace.

Pure: no I/O, so it can be tested on its own. The scheduler stores what it returns.
"""

from __future__ import annotations

import copy
import hashlib
import json
import operator
from collections.abc import Callable
from dataclasses import dataclass, field

from .store import LOOKS, Step

# Golem's own blocks, by type, and their outputs.
CODE = {
    "setup": ("next",), "vet": ("next",), "refresh": ("next",), "push": ("next",),
    "pr": ("next",), "checks": ("pass", "fail"), "merge": ("next",), "deploy": ("pass", "fail"),
    "answer": ("next",), "proposals": ("next",), "test": ("pass", "fail"),
}  # fmt: skip
# Golem's own blocks that ship: one change at a time per project goes from push to deploy (turns.py).
SHIPS = ("push", "pr", "checks", "merge", "deploy")
# Blocks golem passes through on the way, leaving a row each, already done.
CONTROL = ("counter", "condition")
NEXT = "next"  # the one output of a block that doesn't choose
# Agent blocks with a contract return more than a verdict, and some route by it.
CONTRACTS = {"options": ("done",), "design": ("directions", "nothing"), "propose": ("done",)}
TYPES = (
    "start", "end", "input", "output", "agent", "script", "counter", "condition", "checkin", "choose", "container", *CODE,
)  # fmt: skip
# Blocks Riggy may skip from the status page; they then leave by `skip_to` (their first output unless set).
SKIPPABLE = ("agent", "checkin", "choose", "script")
OPERATORS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne}
TURNS = ("build", "ship", "none")
RESERVED = ("blocked",)  # an agent's verdict that isn't a way out: it asks Riggy and carries on
INLINE_LIMIT = 200  # counters and conditions passed in one go before golem calls it a loop
GUARD_VISITS = 8  # visits to one block before a job stops and asks Riggy whether to go on
GUARD_RUNS = 40  # agent runs in one job before the same


class GraphError(Exception):
    """Something about a graph golem can't walk: a missing wire, a container it can't open."""


@dataclass(frozen=True)
class Sub:
    """A workflow at one level of a job: its graph for the tab it resolves to."""

    key: str  # what containers name it by
    name: str
    tab: str  # "" for its default tab
    graph: dict = field(hash=False, compare=False)


# Finds the sub-workflow a container names, for its tab setting and the job's mode.
Resolver = Callable[[str, str, str], Sub]


# --- one graph ----------------------------------------------------------------


def ports(node: dict, outputs: dict[str, list[str]] | None = None) -> list[str]:
    """A block's outputs, in order: the first is its usual way on. A container's are its
    sub-workflow's Output names (`outputs`, by container id); End and Output have none."""
    t = node["type"]
    if t in ("start", "input", "counter"):
        return [NEXT]
    if t in ("end", "output"):
        return []
    if t in CODE:
        return list(CODE[t])
    if t == "condition":
        return ["true", "false"]
    if t == "choose":
        return ["picked", "more"]
    if t == "script":
        return ["pass", "fail"]
    if t == "agent":
        if node.get("contract") in CONTRACTS:
            return list(CONTRACTS[node["contract"]])
        return [o["name"] for o in node.get("outputs") or []] or ["done"]
    if t == "checkin":
        return [o["name"] for o in node.get("outputs") or []] or ["replied"]
    if t == "container":
        return list((outputs or {}).get(node["id"], []))
    raise GraphError(f"unknown block type {t!r}")


def wires(graph: dict) -> dict[str, dict[str, str]]:
    """{block id: {output: the block it's wired to}}."""
    found: dict[str, dict[str, str]] = {}
    for e in graph.get("edges") or []:
        found.setdefault(e["from"], {})[e["port"]] = e["to"]
    return found


def outputs_of(graph: dict) -> list[str]:
    """A sub-workflow's Output names, in the order they're drawn."""
    return [n["name"] for n in graph.get("nodes") or [] if n["type"] == "output"]


def by_id(graph: dict) -> dict[str, dict]:
    return {n["id"]: n for n in graph.get("nodes") or []}


def entry(graph: dict) -> dict:
    """A graph's Start block (Input, in a sub-workflow)."""
    found = [n for n in graph.get("nodes") or [] if n["type"] in ("start", "input")]
    if len(found) != 1:
        raise GraphError("a workflow has exactly one Start block (Input, in a sub-workflow)")
    return found[0]


def digest(value: object) -> str:
    """A short hash of a graph or a job's copy, to tell whether two are the same."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


# --- flattening -----------------------------------------------------------------


def flatten(root: Sub, start: str, mode: str, resolve: Resolver, outputs_ok: bool = False) -> dict:
    """The job's own copy of a workflow: one flat graph, every container opened in place.

    Block ids are their containers' ids and their own, joined by "/". `levels` says which
    workflow and tab each level is (the top is ""), for the running view; `intros` holds each
    level's intro. End blocks of jobs that only look are `silent`: their last block said it.
    `outputs_ok`: flattening a sub-workflow on its own, its Outputs stay, as blocks `out:<name>`."""
    nodes: dict[str, dict] = {}
    raw: dict[str, dict[str, tuple]] = {}  # flat id -> {port: symbolic target}
    starts: dict[str, tuple] = {}  # level path -> where it starts, symbolically
    exits: dict[str, dict[str, tuple]] = {}  # container path -> {its output: where it goes, in its parent}
    levels: dict[str, dict] = {}
    intros: dict[str, str] = {}

    def open_up(sub: Sub, path: str, seen: tuple[str, ...]) -> None:
        g = sub.graph
        levels[path] = {"key": sub.key, "name": sub.name, "tab": sub.tab, "graph": g}  # the graph as drawn, for the running view
        intros[path] = g.get("intro") or ""
        here, wired = by_id(g), wires(g)
        flat = (lambda i: f"{path}/{i}") if path else (lambda i: i)
        outs: dict[str, list[str]] = {}
        for n in g.get("nodes") or []:
            if n["type"] == "container":
                inner = resolve(n["workflow"], n.get("tab") or "auto", mode)
                if inner.key in seen:
                    raise GraphError(f"{inner.name} contains itself")
                outs[n["id"]] = outputs_of(inner.graph)
                open_up(inner, flat(n["id"]), (*seen, inner.key))

        def target(to: str) -> tuple:
            n = here.get(to)
            if n is None:
                raise GraphError(f"a wire goes to {to!r}, which isn't a block in {sub.name}")
            if n["type"] == "output":
                return ("exit", path, n["name"])
            if n["type"] == "container":
                return ("enter", flat(to))
            if n["type"] in ("start", "input"):
                raise GraphError("nothing can be wired into Start")
            return ("node", flat(to))

        for n in g.get("nodes") or []:
            t, out = n["type"], wired.get(n["id"], {})
            missing = [p for p in ports(n, outs) if p not in out]
            if missing and t != "output":
                raise GraphError(f"{n['id']}'s {missing[0]} output isn't wired, in {sub.name}")
            if t in ("start", "input"):
                starts[path] = target(out[NEXT])
            elif t == "container":
                exits[flat(n["id"])] = {p: target(out[p]) for p in outs[n["id"]]}
            elif t != "output":
                nid = flat(n["id"])
                nodes[nid] = {**copy.deepcopy(n), "id": nid, "ref": n["id"], "path": path}
                if t == "end" and start in LOOKS:
                    nodes[nid]["silent"] = True
                raw[nid] = {p: target(out[p]) for p in ports(n, outs)}

    open_up(root, "", (root.key,))

    def settle(sym: tuple, depth: int = 0) -> str:
        if depth > 256:
            raise GraphError("containers wired in a circle")
        kind = sym[0]
        if kind == "node":
            return sym[1]
        if kind == "enter":
            return settle(starts[sym[1]], depth + 1)
        path, name = sym[1], sym[2]
        if path == "":
            if not outputs_ok:
                raise GraphError(f"an Output ({name}) in a workflow that isn't a sub-workflow: use End")
            nodes.setdefault(f"out:{name}", {"id": f"out:{name}", "type": "output", "name": name, "ref": "", "path": ""})
            return f"out:{name}"
        return settle(exits[path][name], depth + 1)

    edges = {nid: {p: settle(sym) for p, sym in outs.items()} for nid, outs in raw.items()}
    return {
        "v": 1, "start": settle(starts[""]), "kind": start, "mode": mode,
        "nodes": nodes, "edges": edges, "levels": levels, "intros": intros,
    }  # fmt: skip


# --- walking a job's copy ---------------------------------------------------------


def blank_vars() -> dict:
    """What a job keeps besides its trace: its counters, Riggy's settings for blocks that haven't
    run yet, what the last block found for the next agent (`context`), and the loop guard."""
    return {"counters": {}, "nodes": {}, "context": "", "guard": {"rounds": 0, "hit": ""}}


def kind_of(node: dict) -> tuple[str, str]:
    """The kind and name of the step row a visit to the block makes."""
    t = node["type"]
    if t == "agent":
        return "agent", node["name"]
    if t == "end":
        return "alert", "alert"
    if t == "counter":
        return "counter", node["counter"]
    if t in ("script", "checkin", "choose", "condition"):
        return t, node.get("name") or t
    return t, t


def turn_of(node: dict) -> str:
    """The turn a block takes on its project (turns.py): its own setting, else by type."""
    if node.get("turn") in TURNS:
        return node["turn"]
    t = node["type"]
    if t in SHIPS:
        return "ship"
    return "build" if t == "agent" else "none"


def counter_key(node: dict, name: str) -> str:
    """Counters belong to the level they're in: each container's are its own."""
    return f"{node['path']}/{name}" if node.get("path") else name


def visits(trace: list[Step], node_id: str) -> int:
    return sum(1 for s in trace if s.node_id == node_id)


def evaluate(node: dict, counters: dict[str, int], trace: list[Step], mode: str, unknown: bool | None = None) -> bool:
    """A condition block's answer: its rows, all or any of them. A counter nothing has set yet is
    0, unless `unknown` says what a row about one comes out as (a prediction shows the work a
    block like vet hasn't counted yet)."""
    results = []
    for row in node.get("rows") or []:
        of, name, op, value = row.get("of"), row.get("name", ""), row.get("op", "=="), row.get("value")
        if of == "mode":
            have, want = mode or "", str(value or "")
            results.append(have == want if op in ("==", "is") else have != want)
            continue
        if of == "visits":
            ref = f"{node['path']}/{name}" if node.get("path") else name
            have = visits(trace, ref)
        else:
            key = counter_key(node, name)
            if unknown is not None and key not in counters:
                results.append(unknown)
                continue
            have = counters.get(key, 0)
        try:
            results.append(OPERATORS[op](have, int(value)))
        except (KeyError, TypeError, ValueError):
            results.append(False)
    if not results:
        return True
    return all(results) if node.get("join", "all") == "all" else any(results)


def row(snap: dict, node_id: str, trace: list[Step], vars: dict, job_id: int = 0) -> Step:
    """The step row for a visit to a block, still to do."""
    node = snap["nodes"][node_id]
    kind, name = kind_of(node)
    step = Step(
        0, job_id, 0, kind, name, max_turns=node.get("turns") or None, optional=bool(node.get("optional")),
        turn=turn_of(node), node_id=node_id, visit=visits(trace, node_id) + 1,
    )  # fmt: skip
    if kind == "agent" and vars.get("context"):
        step.problem = vars["context"]
    return step


@dataclass
class Advanced:
    """What walking on from a block gives: the rows it leaves (control blocks passed, skipped
    blocks, the next block to do), the job's vars after, and whether the job is finished."""

    rows: list[Step]
    vars: dict
    finished: bool = False

    @property
    def pending(self) -> Step | None:
        return next((r for r in self.rows if r.state == "pending"), None)


def advance(snap: dict, vars: dict, trace: list[Step], node_id: str | None, port: str | None, job_id: int = 0,
            guard: tuple[int, int] = (GUARD_VISITS, GUARD_RUNS)) -> Advanced:  # fmt: skip
    """Walk on from block `node_id` by its output `port` (from the start, with None): through
    counters, conditions and blocks Riggy skipped, to the next block to do, or the end."""
    vars = copy.deepcopy(vars)
    trace = list(trace)
    rows: list[Step] = []
    to = snap["start"] if node_id is None else snap["edges"][node_id][port]
    agent = node_id is not None and snap["nodes"].get(node_id, {}).get("type") == "agent"
    if agent:
        vars["context"] = ""  # the agent that just ran had what the last blocks found
    for _ in range(INLINE_LIMIT):
        node = snap["nodes"][to]
        t = node["type"]
        if t == "output":  # a sub-workflow walked on its own: it ends at its Outputs
            return Advanced(rows, vars, finished=True)
        if t == "end" and node.get("silent"):
            return Advanced(rows, vars, finished=True)
        if t in CONTROL:
            if t == "counter":
                key = counter_key(node, node["counter"])
                vars["counters"][key] = vars["counters"].get(key, 0) + 1
                out = NEXT
            else:
                out = "true" if evaluate(node, vars["counters"], trace, snap.get("mode", "")) else "false"
            done = row(snap, to, trace, vars, job_id)
            done.problem, done.state, done.port = None, "done", out
            done.summary = f"{node['counter']}: {vars['counters'][counter_key(node, node['counter'])]}" if t == "counter" else out
            rows.append(done)
            trace.append(done)
            to = snap["edges"][to][out]
            continue
        settings = vars["nodes"].get(to, {})
        if settings.get("skip") and t in SKIPPABLE:
            out = node.get("skip_to") or ports(node)[0]
            skipped = row(snap, to, trace, vars, job_id)
            skipped.problem, skipped.state, skipped.port, skipped.summary = None, "skipped", out, SKIPPED
            rows.append(skipped)
            trace.append(skipped)
            to = snap["edges"][to][out]
            continue
        nxt = row(snap, to, trace, vars, job_id)
        if settings.get("model"):
            nxt.model = settings["model"]
        if settings.get("effort"):
            nxt.effort = settings["effort"]
        rounds = vars["guard"].get("rounds", 0) + 1
        runs = sum(1 for s in trace if s.kind == "agent" and s.started_at) + (1 if t == "agent" else 0)
        if nxt.visit > guard[0] * rounds or runs > guard[1] * rounds:
            vars["guard"]["hit"] = to
        rows.append(nxt)
        return Advanced(rows, vars)
    raise GraphError("counters and conditions wired in a circle with nothing between them")


SKIPPED = "Skipped from the status page."  # control.SKIPPED's words, for a block skipped before it ran


def predict(snap: dict, vars: dict, trace: list[Step], node_id: str | None) -> list[str]:
    """The blocks likely to come after `node_id` (from the start, with None), in order: each
    block's first output (its `skip_to`, if Riggy skipped it), conditions as they'd be now.
    Stops at the end or a block already predicted: loops show once."""
    seen: set[str] = set()
    path: list[str] = []
    counters = dict(vars.get("counters") or {})
    at = snap["start"] if node_id is None else _first(snap, vars, node_id)
    for _ in range(len(snap["nodes"]) * 2 + 2):
        if at is None or at in seen:
            break
        node = snap["nodes"][at]
        if node["type"] in ("output",) or (node["type"] == "end" and node.get("silent")):
            break
        seen.add(at)
        if node["type"] == "counter":
            key = counter_key(node, node["counter"])
            counters[key] = counters.get(key, 0) + 1
            at = snap["edges"][at][NEXT]
            continue
        if node["type"] == "condition":
            at = snap["edges"][at]["true" if evaluate(node, counters, trace, snap.get("mode", ""), unknown=True) else "false"]
            continue
        path.append(at)
        at = _first(snap, vars, at)
    return path


def _first(snap: dict, vars: dict, node_id: str) -> str | None:
    """Where a block usually goes: its first output, or `skip_to` if Riggy skipped it."""
    node = snap["nodes"][node_id]
    outs = ports(node)
    if not outs:
        return None
    out = outs[0]
    if (vars.get("nodes") or {}).get(node_id, {}).get("skip") and node["type"] in SKIPPABLE:
        out = node.get("skip_to") or out
    return snap["edges"][node_id][out]


def happy_path(snap: dict) -> list[str]:
    """From the start, every block's first output (conditions take `true`): the way a job goes
    when nothing fails and nobody skips anything. Counters and conditions are left out."""
    seen, path, at = set(), [], snap["start"]
    while at is not None and at not in seen:
        seen.add(at)
        node = snap["nodes"][at]
        if node["type"] == "output" or (node["type"] == "end" and node.get("silent")):
            break
        if node["type"] not in CONTROL:
            path.append(at)
        outs = ports(node)
        at = snap["edges"][at][outs[0]] if outs else None
    return path
