"""Golem's prompts as reviewed: what each built-in agent block is told (golem/golem/compose.py)
is held to `golden/composed/`, so any change to one shows as a diff."""

from __future__ import annotations

from pathlib import Path

from golem import compose

GOLDEN = Path(__file__).parent / "golden"


def test_what_each_builtin_block_is_told_is_as_reviewed():
    fresh = compose.composed()
    kept = {p.name: p.read_text() for p in (GOLDEN / "composed").glob("*.md")}
    changed = sorted(n for n in fresh.keys() | kept.keys() if fresh.get(n) != kept.get(n))
    assert not changed, (
        f"What these blocks are told changed: {', '.join(changed)}. If you meant it, run `just compose-builtins` "
        "and review `git diff golem/tests/golden/composed` before committing."
    )
