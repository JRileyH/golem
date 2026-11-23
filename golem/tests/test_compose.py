"""Golem's prompts as reviewed: what each built-in agent block is told (golem/golem/compose.py)
is held to `golden/composed/`, so any change to one shows as a diff; and nothing the old
playbooks told a step is lost without saying so (`golden/must_keep.yml`)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from test_workflows import CASES, REPLACED, golden, old_steps, snapshot

from golem import compose, engine

GOLDEN = Path(__file__).parent / "golden"
KEEP = yaml.safe_load((GOLDEN / "must_keep.yml").read_text())
NAMED = re.compile(r"`[^`\n]+`|skills/[\w-]+\.md")


def test_what_each_builtin_block_is_told_is_as_reviewed():
    fresh = compose.composed()
    kept = {p.name: p.read_text() for p in (GOLDEN / "composed").glob("*.md")}
    changed = sorted(n for n in fresh.keys() | kept.keys() if fresh.get(n) != kept.get(n))
    assert not changed, (
        f"What these blocks are told changed: {', '.join(changed)}. If you meant it, run `just compose-builtins` "
        "and review `git diff golem/tests/golden/composed` before committing."
    )


def sentences(text: str) -> list[str]:
    """Text as its sentences and list items, each on one line, numbering and bullets taken off."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        for item in re.split(r"\n(?=\s*(?:\d+\.|[-*])\s)", para):
            item = re.sub(r"^\s*(?:\d+\.|[-*])\s+", "", " ".join(item.split()))
            out += [s for s in re.split(r"(?<=[.!?:])\s+(?=[A-Z`\"(*])", item) if len(s) > 3]
    return out


def flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("book, mode", CASES)
def test_nothing_a_playbook_told_a_step_is_lost_without_saying_why(book, mode):
    from golem import workflows

    builtins = workflows.load_all(compose.REPO / "workflows")
    snap = snapshot(builtins, REPLACED[(book, mode)][0], mode)
    path = engine.happy_path(snap)
    lost = []
    # The playbook's intro, which every step got: still the workflow's, or said where it went.
    intro, everything = snap["intros"].get("") or "", ""
    for nid in path:
        if snap["nodes"][nid].get("type") == "agent":
            everything += "\n\n" + compose.told(snap, path, nid)
    lost += _lost(f"{book}.intro", golden()[book]["intro"], flat(intro), flat(intro + everything), mode)
    for nid, old in zip(path, old_steps(book, mode)):
        if not old["instructions"]:
            continue
        node = snap["nodes"][nid]
        intro = "\n\n".join(t for t in (snap["intros"].get(""), snap["intros"].get(node.get("path") or "")) if t)
        now = flat(intro + "\n\n" + compose.told(snap, path, nid))
        lost += _lost(f"{book}.{old['name']}", old["instructions"], now, now, mode)
    assert not lost, "Said no more, and not in golden/must_keep.yml:\n" + "\n".join(lost)


def _lost(where: str, old: str, now: str, carried_in: str, mode: str) -> list[str]:
    """What of `old` isn't in `now` (flattened), and isn't carried (in `carried_in`: the words, or
    the words in each mode) or dropped by golden/must_keep.yml under `where`."""
    carried, dropped = KEEP["carried"].get(where) or {}, KEEP["dropped"].get(where) or {}
    lost = []
    for s in sentences(old):
        if flat(s) in now or any(s.startswith(k) for k in dropped):
            continue
        by = next((v for k, v in carried.items() if s.startswith(k)), None)
        by = by.get(mode) if isinstance(by, dict) else by
        if by is not None and flat(by) in carried_in:
            continue
        lost.append(f"{where}: “{s}”")
    for name in set(NAMED.findall(old)):
        if name not in now and not any(name in k for k in dropped):
            lost.append(f"{where}: no longer names {name}")
    return lost
