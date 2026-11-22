"""Workflows: the built-ins behave as the playbooks they replace did.

`golden/playbooks.json` is the playbooks frozen as they were before workflows: for each one,
its intro and, for each mode, the steps a job got (kind, name, turns, optional, instructions).
The built-in workflows must walk the same steps on their happy path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from golem import playbook
from golem.store import MODES

REPO = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "golden" / "playbooks.json"
PLAYBOOKS = ("reckless", "standard", "rigorous", "feature", "options", "design", "question", "proposals", "adopt")


def frozen() -> dict:
    """The playbooks as they are on disk, in the golden file's shape."""
    found = {}
    for name in PLAYBOOKS:
        book = playbook.load(REPO / "playbooks" / f"{name}.md")
        found[name] = {
            "intro": book.intro,
            "modes": {
                mode: [
                    {"kind": s.kind, "name": s.name, "turns": s.max_turns, "optional": s.optional, "instructions": s.instructions}
                    for s in book.for_mode(mode)
                ]
                for mode in MODES
            },
        }
    return found


def golden() -> dict:
    return json.loads(GOLDEN.read_text())


def test_the_golden_playbooks_are_the_playbooks():
    """While the playbooks are still on disk, the frozen copy matches them. GOLDEN_WRITE=1 writes it."""
    if not (REPO / "playbooks").is_dir():
        return
    if os.environ.get("GOLDEN_WRITE") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(frozen(), indent=1, ensure_ascii=False) + "\n")
    assert frozen() == golden()
