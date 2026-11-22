"""Jobs under way from before workflows: their steps become a graph of their own.

Before workflows, a job was a list of steps written from its playbook, with golem's runtime
rules on top: a fix and the steps from the last push again after CI or the deployer failed,
another round after a pick asked for one, the rest of a design pass passed over when its round
found nothing, a check-in's reply aimed past the design pass at the buildout, an adoption's
worker step skipped when the check found nothing to fix. Converting a job turns its steps into
blocks in a line and those rules into wiring, so it carries on exactly as it would have, walked
like any job (engine.py). Steps still to come are forgotten once their settings (a model, a skip,
notes) are kept for their blocks: walking on writes them again.
"""

from __future__ import annotations

import json
import logging

from . import engine, plan
from .store import LOOKS, OPEN, Job, Step, Store

log = logging.getLogger(__name__)

MAX_FIXES = 4  # fixes before asking Riggy for a hint, as golem's old rule had it
DESIGN_PASS = ("design", "guide", "apply")
INSERTED = ("sync", "resolve")  # golem's own, put in by the engine as it goes: never blocks


def convert(store: Store, job: Job) -> Job:
    """Give a job from before workflows its own graph, built from its steps. Returns it."""
    steps = store.steps(job.id)
    project = store.project(job.slug)
    key, mode = plan.workflow_for(job, project)
    built = _builtin(store, key, mode)
    ship = _builtin(store, "ship", "")
    open_ = [s for s in steps if s.state in OPEN and s.name not in INSERTED]
    current = open_[0] if open_ else None
    blocks = [s for s in steps if s.name not in INSERTED]
    nodes: dict[str, dict] = {}
    edges: dict[str, dict[str, str]] = {}
    ids = {s.position: f"s{s.position}" for s in blocks}
    order = [ids[s.position] for s in blocks]
    for s in blocks:
        nodes[ids[s.position]] = _node(store, s, ids[s.position], built, ship)
    for i, s in enumerate(blocks):
        nid = ids[s.position]
        outs = engine.ports(nodes[nid])
        if outs:
            edges[nid] = {outs[0]: order[i + 1] if i + 1 < len(order) else _end(nodes, order, job)}
    _wire_rules(blocks, ids, nodes, edges, order, ship)
    for nid, n in nodes.items():  # any other output goes where the first does, as the list did
        outs = engine.ports(n)
        for p in outs[1:]:
            edges[nid].setdefault(p, edges[nid][outs[0]])
    intro = (built or {}).get("intro", "")  # the playbook's, word for word (tests/test_workflows.py)
    snap = {
        "v": 1, "start": order[0] if order else "", "kind": job.start, "mode": mode, "nodes": nodes, "edges": edges,
        "levels": {"": {"key": key, "name": f"{key} (from before workflows)", "tab": mode}}, "intros": {"": intro}, "legacy": True,
    }  # fmt: skip
    vars = engine.blank_vars()
    vars["counters"]["fixes"] = sum(1 for s in steps if s.kind == "agent" and s.name == "fix")
    later = [s for s in steps if current is not None and s.position > current.position]
    for s in later:  # what Riggy set for steps still to come is kept for their blocks
        nid = ids.get(s.position)
        if nid is None:
            continue
        mine = {k: v for k, v in (("model", s.model), ("effort", s.effort), ("skip", s.state == "skipped" or None)) if v}
        if mine:
            vars["nodes"][nid] = mine
        store.db.execute("UPDATE notes SET node_id = ?, step_id = NULL WHERE step_id = ?", (nid, s.id))
    with store.db:
        store.db.execute("BEGIN")
        for s in steps:
            if s.position in ids and (current is None or s.position <= current.position):
                port = "fail" if s.state == "failed" and s.kind in ("checks", "deploy") else (engine.ports(nodes[ids[s.position]]) or [""])[0]
                store.db.execute(
                    "UPDATE steps SET node_id = ?, port = ?, visit = 1, turn = ? WHERE id = ?",
                    (ids[s.position], port if s.state not in OPEN else "", s.turn or plan.turn_of(steps, s), s.id),
                )
        if current is not None:
            store.db.execute("DELETE FROM steps WHERE job_id = ? AND position > ? AND started_at IS NULL", (job.id, current.position))
        store.db.execute(
            "UPDATE jobs SET workflow = ?, mode = ?, graph = ?, graph_hash = ?, vars = ? WHERE id = ?",
            (key, mode, json.dumps(snap), engine.digest(snap), json.dumps(vars), job.id),
        )
    log.info("job %s: carried on as a workflow of its own (%d blocks)", job.id, len(nodes))
    return store.job(job.id)


def _builtin(store: Store, key: str, mode: str) -> dict | None:
    wf = store.workflow(key=key)
    tab = (store.tab(wf.id, mode) or store.tab(wf.id, "")) if wf else None
    return tab.working if tab else None


def _instructions(store: Store, graph: dict | None, name: str, depth: int = 0) -> str:
    """An agent block's instructions in a built-in's graph, or in the sub-workflows it contains."""
    if not graph or depth > 4:
        return ""
    for n in graph["nodes"]:
        if n["type"] == "agent" and n.get("name") == name:
            return n.get("instructions") or ""
    for n in graph["nodes"]:
        if n["type"] == "container" and (found := _instructions(store, _builtin(store, n["workflow"], ""), name, depth + 1)):
            return found
    return ""


def _node(store: Store, s: Step, nid: str, built: dict | None, ship: dict | None) -> dict:
    base = {"id": nid, "ref": nid, "path": ""}
    if s.kind == "agent":
        text = _instructions(store, ship if s.name == "fix" else built, s.name)
        node = {**base, "type": "agent", "name": s.name, "instructions": text, "turns": s.max_turns or 150,
                "optional": s.optional, "turn": s.turn or None}  # fmt: skip
        if s.name in ("options", "design"):
            node["contract"] = s.name
            if s.name == "design" and s.optional:
                node["skip_to"] = "nothing"
        return {k: v for k, v in node.items() if v is not None}
    if s.kind == "alert":
        return {**base, "type": "end"}
    if s.kind in ("checkin", "choose"):
        return {**base, "type": s.kind, "name": s.kind, "optional": s.optional}
    return {**base, "type": s.kind}


def _end(nodes: dict, order: list[str], job: Job) -> str:
    """Where a list that doesn't end with golem's alert ends: a question's or proposals' last step."""
    if "end" not in nodes:
        nodes["end"] = {"id": "end", "ref": "end", "path": "", "type": "end", **({"silent": True} if job.start in LOOKS else {})}
        order.append("end")
    return "end"


def _wire_rules(blocks: list[Step], ids: dict[int, str], nodes: dict, edges: dict, order: list[str], ship: dict | None) -> None:
    """Golem's old runtime rules, as wiring."""
    fix_text = next((n.get("instructions", "") for n in (ship or {}).get("nodes", []) if n.get("name") == "fix"), "")
    for i, s in enumerate(blocks):
        nid = ids[s.position]
        if s.kind in ("checks", "deploy"):  # a fix, then the steps from the last push again
            pushes = [ids[b.position] for b in blocks[: i + 1] if b.kind in ("push", "push-main")]
            back = pushes[-1] if pushes else nid
            loop = f"{back}-fix"
            if f"{loop}-tired" not in nodes:
                nodes[f"{loop}-tired"] = {"id": f"{loop}-tired", "ref": f"{loop}-tired", "path": "", "type": "condition", "name": "tired",
                                          "join": "all", "rows": [{"of": "counter", "name": "fixes", "op": ">=", "value": MAX_FIXES}]}  # fmt: skip
                nodes[f"{loop}-hint"] = {"id": f"{loop}-hint", "ref": f"{loop}-hint", "path": "", "type": "checkin", "name": "hint",
                                         "reply_to": f"{loop}-agent", "turn": "ship",
                                         "message": "I've tried to fix $failed $fixes times and it still fails: $failure.\n\n"
                                         "Reply with a hint and I'll try again, or `!kill` to drop the job."}  # fmt: skip
                nodes[f"{loop}-agent"] = {"id": f"{loop}-agent", "ref": f"{loop}-agent", "path": "", "type": "agent", "name": "fix",
                                          "instructions": fix_text, "turns": 150, "turn": "ship"}  # fmt: skip
                nodes[f"{loop}-count"] = {"id": f"{loop}-count", "ref": f"{loop}-count", "path": "", "type": "counter", "counter": "fixes"}
                edges[f"{loop}-tired"] = {"true": f"{loop}-hint", "false": f"{loop}-agent"}
                edges[f"{loop}-hint"] = {"replied": f"{loop}-agent"}
                edges[f"{loop}-agent"] = {"done": f"{loop}-count"}
                edges[f"{loop}-count"] = {"next": back}
            edges[nid]["fail"] = f"{loop}-tired"
        if s.kind == "choose":  # another round goes back to the step that made them
            makers = [ids[b.position] for b in blocks[:i] if b.kind == "agent" and b.name in ("options", "design")]
            edges[nid]["more"] = makers[-1] if makers else nid
        if s.kind == "agent" and s.name == "design":  # nothing to design: past the rest of the pass
            rest = [j for j, b in enumerate(blocks) if j > i and b.kind == "agent" and b.name in ("guide", "apply")]
            after = rest[-1] + 1 if rest else i + 1
            edges[nid]["nothing"] = order[after] if after < len(order) else order[-1]
        if s.kind == "checkin":  # his reply past the design pass, to the step that builds
            later = [b for b in blocks[i + 1 :] if b.kind == "agent"]
            builds = next((b for b in later if b.name not in DESIGN_PASS), None)
            if later and builds and later[0].name in DESIGN_PASS:
                nodes[nid]["reply_to"] = ids[builds.position]
        if s.kind == "vet" and s.state in OPEN:  # the worker step only if the check finds gaps
            work = next((b for b in blocks[i + 1 :] if b.kind == "agent" and b.name == "adopt"), None)
            setup = next((b for b in blocks[i + 1 :] if b.kind == "setup"), None)
            if work and setup:
                j = blocks.index(work)
                cond = f"{ids[work.position]}-gaps"
                nodes[cond] = {"id": cond, "ref": cond, "path": "", "type": "condition", "name": "gaps", "join": "all",
                               "rows": [{"of": "counter", "name": "gaps", "op": ">", "value": 0}]}  # fmt: skip
                edges[cond] = {"true": ids[work.position], "false": order[j + 1] if j + 1 < len(order) else order[-1]}
                edges[ids[setup.position]] = {"next": cond}
