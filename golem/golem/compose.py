"""What each agent block of golem's built-in workflows is told, put together as a job would put
it together: the workflow's text every block gets, then the block's own (a stage block's from
golem's parts for its settings, with what it'd be about on the workflow's usual way through).

`tests/golden/composed/` keeps these as reviewed, a file a block, and a test holds the built-ins
to them, so every change to a prompt shows up as a diff to read before it ships. After a change
you mean, write them again and review the diff:

    just compose-builtins        # uv run python -m golem.compose

Pure but for reading golem's files.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from . import about, engine, stages, workflows
from .store import Step

REPO = Path(__file__).resolve().parents[2]
FOLDER = REPO / "golem" / "tests" / "golden" / "composed"


def composed(folder: Path = REPO / "workflows") -> dict[str, str]:
    """Every built-in workflow: `<key>._intro.md` (what every block gets) and `<key>.<block>.md`
    for each of its own agent blocks (a container's are its workflow's)."""
    found = workflows.load_all(folder)
    stages.use(folder / "stages")
    out = {}
    for key, wf in sorted(found.items()):
        sub = wf.start == "sub"
        root = engine.Sub(key, wf.name, "", wf.graph) if sub else workflows.top(found, key, "")
        snap = engine.flatten(root, "change" if sub else wf.start, "", workflows.resolver(found), outputs_ok=sub)
        out[f"{key}._intro.md"] = (snap["intros"].get("") or "").strip() + "\n"
        path = engine.happy_path(snap)
        order = path + [n for n in snap["nodes"] if n not in path]
        for nid in order:
            node = snap["nodes"][nid]
            if node.get("type") != "agent" or node.get("path"):
                continue
            out[f"{key}.{nid}.md"] = told(snap, path, nid).strip() + "\n"
    return out


def told(snap: dict, path: list[str], nid: str) -> str:
    """One block's own text: a custom agent's instructions, or a stage block's put together, about
    what it would be on the usual way through (each block before it done, saying so)."""
    node = snap["nodes"][nid]
    stage = stages.catalog().get(node.get("stage") or "")
    if stage is None:
        return node.get("instructions") or ""
    before = path[: path.index(nid)] if nid in path else path
    trace = []
    for i, prior in enumerate(before, start=1):
        kind, name = engine.kind_of(snap["nodes"][prior])
        head = hashlib.sha1(prior.encode()).hexdigest()  # a stand-in commit for each block's work
        trace.append(Step(i, 1, i, kind, name, state="done", node_id=prior, head=head, summary=f"(what {name} said)"))
    here = Step(len(trace) + 1, 1, len(trace) + 1, "agent", node.get("name") or nid, node_id=nid)
    found = about.about(snap["nodes"], trace, here, "")
    golems = stages.golems_text().get(stage.key, {})
    parts = {p: golems[p] for p in stage.every_part(node) if golems.get(p)}
    return stages.compose(node, parts, found.text, found.variant)


def write(folder: Path = FOLDER) -> list[str]:
    """The snapshots written again, those no block has any more removed; what changed."""
    folder.mkdir(parents=True, exist_ok=True)
    fresh, changed = composed(), []
    for old in folder.glob("*.md"):
        if old.name not in fresh:
            old.unlink()
            changed.append(f"- {old.name}")
    for name, text in fresh.items():
        path = folder / name
        if not path.exists() or path.read_text() != text:
            changed.append(("~ " if path.exists() else "+ ") + name)
            path.write_text(text)
    return changed


if __name__ == "__main__":
    changes = write()
    print("\n".join(changes) or "Nothing changed.")
    sys.exit(0)
