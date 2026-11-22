"""Workflows drawn: what the browser's graph (static/flow.js) is given to draw, for a workflow's
tab or a job walking its copy of one, and the pages around it.

The server says everything about a block (its label, its outputs, its state in a job, where
tapping it goes); the script only draws and, in the editor, edits. A job's graph is drawn a level
at a time: its workflow, or a container opened up (`path`), with each container lit by the blocks
inside it.
"""

from __future__ import annotations

import json
from html import escape

from . import engine, layout, plan
from .config import Settings
from .store import ACTIVE, OPEN, Job, Step, Store, Workflow

CODE_NOTES = {
    "setup": "repo, checkout and issue", "vet": "golem's checks of a repo", "refresh": "the checkout up to date",
    "push": "the job's branch", "push-main": "straight to main", "pr": "a pull request", "checks": "waits for CI",
    "merge": "the PR into main", "deploy": "the deployer puts it live", "answer": "sends the answer",
    "proposals": "posts the proposals",
}  # fmt: skip
START_NAMES = {"new": "new project", "change": "change", "look": "question", "propose": "proposals", "adopt": "adoption", "sub": "sub-workflow"}


def label(node: dict, subs: dict[str, str] | None = None) -> tuple[str, str]:
    """A block's name on the graph, and the line under it."""
    t = node["type"]
    if t == "start":
        return "Start", ""
    if t == "input":
        return "Input", "where a container goes in"
    if t == "end":
        return "End", "tells Riggy it's done"
    if t == "output":
        return node.get("name") or "Output", "an output of the container"
    if t == "agent":
        bits = [f"agent · {node.get('turns', '?')} turns"]
        if node.get("contract"):
            bits.append(node["contract"])
        if node.get("optional"):
            bits.append("optional")
        return node.get("name") or "agent", " · ".join(bits)
    if t == "script":
        return node.get("name") or "script", f"$ {node.get('command', '')}"[:60]
    if t == "counter":
        return f"Count {node.get('counter', '')}", "adds one"
    if t == "condition":
        rows = [f"{r.get('name') or r.get('of')} {r.get('op')} {r.get('value')}" for r in node.get("rows") or []]
        return node.get("name") or "condition", (" and " if node.get("join", "all") == "all" else " or ").join(rows) or "always true"
    if t == "checkin":
        return node.get("name") or "check-in", "asks Riggy" + (" · optional" if node.get("optional") else "")
    if t == "choose":
        return node.get("name") or "pick", "Riggy picks an option" + (" · optional" if node.get("optional") else "")
    if t == "container":
        name = (subs or {}).get(node.get("workflow", ""), node.get("workflow", ""))
        tab = node.get("tab") or "auto"
        return node.get("name") or name, f"sub-workflow · {'tab for the mode' if tab == 'auto' else tab + ' tab'}"
    name = plan.LABELS.get(t, t)
    return name[:1].upper() + name[1:], CODE_NOTES.get(t, "golem's own")


def info(node: dict) -> list[list[str]]:
    """What the viewer lists for a block when it's tapped: its settings, as [label, value]."""
    t, found = node["type"], []
    keep = {
        "agent": ("name", "turns", "model", "effort", "turn", "contract", "optional", "skip_to"),
        "script": ("name", "command", "timeout", "turn", "optional"),
        "checkin": ("name", "message", "reply_to", "turn", "optional"),
        "choose": ("name", "optional"),
        "counter": ("counter",),
        "container": ("workflow", "tab"),
        "output": ("name",),
    }.get(t, ())
    for key in keep:
        if node.get(key) not in (None, "", False):
            found.append([key.replace("_", " "), str(node[key])])
    for o in node.get("outputs") or []:
        found.append([f"output {o['name']}", o.get("about") or ""])
    if t == "condition":
        for r in node.get("rows") or []:
            found.append(["when", f"{r.get('of')} {r.get('name') or ''} {r.get('op')} {r.get('value')}".replace("  ", " ")])
    if node.get("instructions"):
        found.append(["instructions", node["instructions"]])
    return found


def drawn(graph: dict, outputs: dict[str, list[str]], subs: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """A graph's blocks and wires as the script draws them."""
    graph = layout.place({**graph, "nodes": [dict(n) for n in graph.get("nodes") or []]})
    nodes = []
    for n in graph["nodes"]:
        name, note = label(n, subs)
        nodes.append({"id": n["id"], "type": n["type"], "label": name, "note": note, "x": n.get("x", 0), "y": n.get("y", 0),
                      "ports": engine.ports(n, outputs), "info": info(n)})  # fmt: skip
    edges = [{"from": e["from"], "port": e["port"], "to": e["to"]} for e in graph.get("edges") or []]
    return nodes, edges


# --- a workflow's tab ---------------------------------------------------------------


def workflow_view(store: Store, wf: Workflow, mode: str) -> dict:
    """A workflow's tab to draw, with what's wrong with it marked."""
    tab = store.tab(wf.id, mode) or store.tab(wf.id, "")
    subs = {w.key: w.name for w in store.workflows()}
    outputs = {}
    for n in tab.graph.get("nodes") or []:
        if n["type"] == "container" and (sub := store.workflow(key=n.get("workflow", ""))):
            default = store.tab(sub.id, "")
            outputs[n["id"]] = engine.outputs_of(default.working) if default else []
    nodes, edges = drawn(tab.graph, outputs, subs)
    problems: dict[str, list[str]] = {}
    for p in tab.problems:
        problems.setdefault(p.get("node") or "", []).append(p["message"])
    for n in nodes:
        n["problems"] = problems.get(n["id"], [])
        if n["type"] == "container" and (sub := store.workflow(key=next(x for x in tab.graph["nodes"] if x["id"] == n["id"]).get("workflow", ""))):
            n["open"] = f"/workflows/{sub.id}"
    return {"nodes": nodes, "edges": edges, "problems": problems.get("", []), "poll": 0}


# --- a job --------------------------------------------------------------------------

STATE = {"running": "now", "waiting": "waiting", "done": "done", "failed": "failed", "skipped": "skipped", "pending": "next"}


def job_view(settings: Settings, store: Store, job: Job, path: str = "") -> dict | None:
    """A level of the job's copy of its workflow to draw, lit by where the job is: each block's
    state, how many times it's run, and its sheet. None once the job's over (its copy is gone)."""
    snap = plan.snapshot(job) or (plan.preview(settings, store, job) if job.state in ACTIVE else None)
    if snap is None:
        return None
    levels = snap.get("levels") or {}
    level = levels.get(path) if path in levels else levels.get("")
    if level is None:
        return None
    graph = level.get("graph") or _flat_graph(snap)
    outputs = {}
    for n in graph.get("nodes") or []:
        if n["type"] == "container":
            inner = levels.get(_join(path, n["id"]), {}).get("graph") or {}
            outputs[n["id"]] = engine.outputs_of(inner)
    subs = {k: v.get("name", "") for k, v in levels.items()}
    nodes, edges = drawn(graph, outputs, {n.get("workflow", ""): subs.get(_join(path, n["id"]), "") for n in graph.get("nodes") or [] if n["type"] == "container"})
    trace = store.steps(job.id)
    ahead = {s.node_id: s for s in plan.planned(settings, store, job) if not s.id and s.node_id}
    for n in nodes:
        flat = _join(path, n["id"])
        if n["type"] == "container":
            inside = [s for s in trace if s.node_id.startswith(flat + "/")]
            todo = any(k.startswith(flat + "/") for k in ahead)
            n["state"], n["visits"] = _combined(inside, todo), 0
            n["open"] = f"/jobs/{job.id}/flow?path={flat}"
            continue
        mine = [s for s in trace if s.node_id == flat]
        if n["type"] in ("start", "input"):
            n["state"] = "done" if trace else ""
        elif mine:
            last = mine[-1]
            n["state"] = STATE.get(last.state, "")
            n["visits"] = sum(1 for s in mine if s.state not in OPEN or s.started_at)
            n["href"] = f"/steps/{last.id}"
        elif flat in ahead:
            n["state"] = "skipped" if ahead[flat].state == "skipped" else "todo"
            n["href"] = f"/jobs/{job.id}/planned/{ahead[flat].position}"
        elif n["type"] == "end" and job.state == "done":
            n["state"] = "done"
    crumbs = [{"label": levels.get("", {}).get("name") or "Workflow", "href": f"/jobs/{job.id}/flow"}]
    parts = [p for p in path.split("/") if p] if path in levels else []
    for i in range(len(parts)):
        sub = "/".join(parts[: i + 1])
        crumbs.append({"label": levels.get(sub, {}).get("name") or parts[i], "href": f"/jobs/{job.id}/flow?path={sub}"})
    note = ""
    if job.graph and job.workflow:
        changed = _current_digest(settings, store, job)
        if changed and changed != job.graph_hash:
            note = "Started before this workflow was last changed: it carries on as it was."
    return {"nodes": nodes, "edges": edges, "crumbs": crumbs, "note": note, "poll": 10 if job.state in ACTIVE else 0}


def _join(path: str, nid: str) -> str:
    return f"{path}/{nid}" if path else nid


def _combined(inside: list[Step], todo: bool) -> str:
    """A container's state, from the steps inside it."""
    states = {s.state for s in inside}
    for state, shown in (("running", "now"), ("waiting", "waiting"), ("pending", "next")):
        if state in states:
            return shown
    if inside:
        return "todo" if todo else ("failed" if inside[-1].state == "failed" else "done")
    return "todo" if todo else ""


def _flat_graph(snap: dict) -> dict:
    """A job's copy from before workflows had levels: its flat blocks, laid out."""
    nodes = [{"id": "start", "type": "start"}] + [{k: v for k, v in n.items() if k not in ("x", "y")} for n in snap["nodes"].values()]
    edges = [{"from": "start", "port": engine.NEXT, "to": snap["start"]}]
    edges += [{"from": nid, "port": p, "to": to} for nid, outs in snap["edges"].items() for p, to in outs.items()]
    return layout.place({"nodes": nodes, "edges": edges})


def _current_digest(settings: Settings, store: Store, job: Job) -> str:
    """The digest a job's copy would have if it were made now: "" if it can't be."""
    from .workflows import Library

    wf = store.workflow(key=job.workflow)
    if wf is None:
        return ""
    try:
        snap = Library(store, settings.process_dir / "workflows", settings.web_password).pin(wf, job.start, job.mode)
    except engine.GraphError:
        return ""
    return engine.digest(snap)


# --- pages ----------------------------------------------------------------------------


def stage(src: str, mode: str = "run") -> str:
    """Where the graph is drawn: outside the part of the page that refreshes, so panning and
    zooming survive; it fetches `src` itself (every `poll` seconds while a job runs)."""
    return (
        f"<section class=flow data-src='{escape(src)}' data-mode={mode} aria-label='The workflow, drawn'>"
        "<div class=flow-tools><button type=button data-fit>Fit</button><button type=button data-zoom=1.25 aria-label='Zoom in'>+</button>"
        "<button type=button data-zoom=0.8 aria-label='Zoom out'>−</button></div>"
        "<nav class=flow-crumbs></nav><p class=flow-note hidden></p><svg class=flow-canvas role=img></svg>"
        "<aside class=flow-info hidden></aside></section>"
    )


def workflows_page(store: Store, signed: bool) -> str:
    """Every workflow, by where it starts: what each is for, which is the fallback, what's wrong.
    Then the modes, and which a request runs in when it doesn't say."""
    fallback = {s: store.get(f"fallback_{s}") for s in START_NAMES}
    groups = []
    for start, name in START_NAMES.items():
        rows = []
        mine = [w for w in store.workflows() if w.start == start]
        if start != "sub" and len([w for w in mine if not w.archived]) > 1:
            picks = "".join(f"<option value='{escape(w.key)}'{' selected' if fallback.get(start) == w.key else ''}>{escape(w.name)}</option>"
                            for w in mine if not w.archived)  # fmt: skip
            rows.append(
                f"<li class=row-form><span class=muted>When golem can't tell which:</span><form method=post action=/act class=inline>"
                f"<input type=hidden name=action value=fallback-workflow><input type=hidden name=start value={start}>"
                f"<input type=hidden name=back value=/workflows><select name=value onchange='this.form.submit()' aria-label='Fallback'>{picks}</select></form></li>"
            )
        for wf in mine:
            tabs = store.tabs(wf.id)
            broken = any(t.problems for t in tabs.values())
            marks = "".join([
                " <span class=badge>fallback</span>" if fallback.get(start) == wf.key else "",
                " <span class='badge trouble'>has problems</span>" if broken else "",
                " <span class=badge>golem has a newer version</span>" if wf.upstream else "",
                " <span class=badge>archived</span>" if wf.archived else "",
            ])  # fmt: skip
            modes = ", ".join(m for m in tabs if m) or "default only"
            rows.append(
                f"<li><a href='/workflows/{wf.id}'><b>{escape(wf.name)}</b></a>{marks}"
                f"<p class=muted><code>!with {escape(wf.handle)}</code> · tabs: {escape(modes)}</p>"
                + (f"<p>{escape(wf.ingest)}</p>" if wf.ingest else "") + "</li>"
            )
        if rows:
            groups.append(f"<section class=card><h2>Starts a {escape(name)}</h2><ul class=workflows>{''.join(rows)}</ul></section>")
    lead = "<p class=lead>How golem works through each kind of request: blocks wired together, from Start to End.</p>"
    return lead + "".join(groups) + modes_card(store)


def modes_card(store: Store) -> str:
    """Modes: ways of working a request can ask for. Each says what to look for in a request, and
    a workflow with a tab for it runs that tab; one without runs its default."""
    back = "/workflows#modes"
    hidden = lambda **f: "".join(f"<input type=hidden name={k} value='{escape(str(v))}'>" for k, v in {**f, "back": back}.items())  # noqa: E731
    rows = []
    for m in store.modes():
        rows.append(
            f"<li><div class=row-form><b>{escape(m.name)}</b>"
            f"<form method=post action=/act class=rename>{hidden(action='mode-rename', name=m.name)}"
            f"<input name=value value='{escape(m.name)}' required maxlength=20 pattern='[a-z][a-z0-9-]*' aria-label='New name for {escape(m.name)}'><button>Rename</button></form>"
            f"<form method=post action=/act onsubmit='return confirm({escape(json.dumps(f'Delete {m.name}, and every tab workflows have for it?'))})'>"
            f"{hidden(action='mode-delete', name=m.name)}<button class=danger>Delete</button></form></div>"
            f"<form method=post action=/act class=reply>{hidden(action='mode-ingest', name=m.name)}"
            f"<textarea name=value rows=2 maxlength=1000 aria-label='What {escape(m.name)} is for'>{escape(m.ingest)}</textarea><button>Save</button></form></li>"
        )
    current = store.get("fallback_mode") or ""
    picks = "".join(f"<option value='{escape(v)}'{' selected' if current == v else ''}>{escape(label)}</option>"
                    for v, label in [("", "each workflow's default tab"), *((m.name, m.name) for m in store.modes())])  # fmt: skip
    return (
        "<section class='card settings' id=modes><h2>Modes</h2><p class=sub>Ways of working a request can ask for (“be thorough”). "
        "Golem reads each request for one of these, by what each is for; a workflow with a tab for that mode runs it, any other its "
        "default tab. A project's settings can give it a default mode. Say <code>!with change:rigorous …</code> to choose.</p>"
        + (f"<ul class=workflows>{''.join(rows)}</ul>" if rows else "<p class=empty>No modes yet.</p>")
        + f"<form method=post action=/act class=reply>{hidden(action='mode-add')}<input name=value placeholder='A new mode, like careful' required maxlength=20 "
        "pattern='[a-z][a-z0-9-]*' aria-label='New mode'><textarea name=ingest rows=2 maxlength=1000 placeholder='What to look for in a request: “he wants extra care taken”' "
        "aria-label='What it is for'></textarea><button>Add mode</button></form>"
        f"<div class=row-form><span>When a request doesn't say, and its project has no default:</span><form method=post action=/act class=inline>"
        f"{hidden(action='fallback-mode')}<select name=value onchange='this.form.submit()' aria-label='Fallback mode'>{picks}</select></form></div>"
        "</section>"
    )


def workflow_page(store: Store, wf: Workflow, mode: str) -> str:
    """A workflow's head: what it's for, and its tabs (default, and one per mode it has)."""
    tabs = store.tabs(wf.id)
    links = "".join(
        f"<a href='/workflows/{wf.id}{'?mode=' + escape(m) if m else ''}'{' class=on aria-current=page' if m == mode else ''}>{escape(m or 'default')}</a>"
        for m in tabs
    )
    meta = f"<p class=muted>Starts a {escape(START_NAMES.get(wf.start, wf.start))} · <code>!with {escape(wf.handle)}</code></p>"
    ingest = f"<p>{escape(wf.ingest)}</p>" if wf.ingest else ""
    return f"<section class='card workflow-head'>{meta}{ingest}<nav class=flow-tabs aria-label=Tabs>{links}</nav></section>"
