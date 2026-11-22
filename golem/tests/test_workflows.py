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


# --- the built-ins walk the playbooks' steps ------------------------------------

import pytest  # noqa: E402

from golem import engine, plan, validate, workflows  # noqa: E402
from golem.store import Step  # noqa: E402

# Each playbook in each mode, and the workflow and tab that replace it.
REPLACED = {
    ("reckless", m): ("web_app", "reckless") for m in MODES
} | {("standard", m): ("web_app", "") for m in MODES} | {("rigorous", m): ("web_app", "rigorous") for m in MODES}
for book in ("feature", "options", "design"):
    key = {"feature": "change"}.get(book, book)
    REPLACED |= {(book, "reckless"): (key, "reckless"), (book, "standard"): (key, ""), (book, "rigorous"): (key, "rigorous")}
for book in ("question", "proposals", "adopt"):
    REPLACED |= {(book, m): (book, "") for m in MODES}
# Which playbook's steps a new project in a mode got: its mode's own.
CASES = sorted({(book, mode) for book, mode in REPLACED if book not in ("reckless", "standard", "rigorous") or book == mode})


@pytest.fixture(scope="module")
def builtins():
    return workflows.load_all(REPO / "workflows")


def snapshot(builtins, key: str, mode: str) -> dict:
    wf = builtins[key]
    return engine.flatten(workflows.top(builtins, key, mode), wf.start, mode, workflows.resolver(builtins))


def as_step(node: dict) -> dict:
    kind, name = engine.kind_of(node)
    return {"kind": kind, "name": name, "turns": node.get("turns") or None, "optional": bool(node.get("optional")),
            "instructions": node.get("instructions", "") if node["type"] == "agent" else ""}  # fmt: skip


def old_steps(book: str, mode: str) -> list[dict]:
    return [{**s, "turns": s["turns"] or None} for s in golden()[book]["modes"][mode]]


@pytest.mark.parametrize("book, mode", CASES)
def test_each_builtin_walks_the_steps_its_playbook_had(builtins, book, mode):
    key, _ = REPLACED[(book, mode)]
    snap = snapshot(builtins, key, mode)
    path = engine.happy_path(snap)
    assert [as_step(snap["nodes"][n]) for n in path] == old_steps(book, mode)
    assert snap["intros"][""] == golden()[book]["intro"]


@pytest.mark.parametrize("book, mode", CASES)
def test_each_block_takes_the_turn_its_step_did(builtins, book, mode):
    snap = snapshot(builtins, *REPLACED[(book, mode)][:1], mode)
    path = engine.happy_path(snap)
    old = [Step(i, 1, i, s["kind"], s["name"]) for i, s in enumerate(golden()[book]["modes"][mode], start=1)]
    assert [engine.turn_of(snap["nodes"][n]) for n in path] == [plan.turn_of(old, s) for s in old]


@pytest.mark.parametrize("book, mode", CASES)
def test_failures_go_to_a_fix_and_back_to_the_last_push(builtins, book, mode):
    """What golem's fix-and-retry did: a fix, then the steps from the last push again."""
    snap = snapshot(builtins, *REPLACED[(book, mode)][:1], mode)
    nodes, edges = snap["nodes"], snap["edges"]
    path = engine.happy_path(snap)
    for i, nid in enumerate(path):
        if nodes[nid]["type"] not in ("checks", "deploy"):
            continue
        at = edges[nid]["fail"]
        while nodes[at]["type"] in engine.CONTROL:  # fixes so far, and not yet tired of it
            at = edges[at]["false" if nodes[at]["type"] == "condition" else "next"]
        assert nodes[at].get("name") == "fix"
        back = edges[at]["done"]
        while nodes[back]["type"] in engine.CONTROL:
            back = edges[back]["next"]
        pushes = [n for n in path[: i + 1] if nodes[n]["type"] in ("push", "push-main")]
        assert back == pushes[-1]


@pytest.mark.parametrize("book, mode", CASES)
def test_replies_picks_and_empty_design_rounds_go_where_golem_sent_them(builtins, book, mode):
    snap = snapshot(builtins, *REPLACED[(book, mode)][:1], mode)
    nodes, edges = snap["nodes"], snap["edges"]
    path = engine.happy_path(snap)
    for i, nid in enumerate(path):
        n = nodes[nid]
        later_agents = [x for x in path[i + 1 :] if nodes[x]["type"] == "agent"]
        if n["type"] == "checkin" and later_agents and nodes[later_agents[0]]["name"] in ("design", "guide", "apply"):
            builds = next(x for x in later_agents if nodes[x]["name"] not in ("design", "guide", "apply"))
            assert f"{n['path']}/{n['reply_to']}".lstrip("/") == builds  # past the design pass, to the buildout
        if n["type"] == "choose":
            makers = [x for x in path[:i] if nodes[x].get("contract") in ("options", "design")]
            assert edges[nid]["more"] == makers[-1]
        if n.get("contract") == "design":
            apply = path.index(next(x for x in path if nodes[x].get("name") == "apply"))
            assert edges[nid]["nothing"] == path[apply + 1]
            assert n.get("skip_to") in (None, "nothing") and (not n.get("optional") or n["skip_to"] == "nothing")


def test_every_builtin_tab_is_valid(builtins):
    cat = workflows.catalog(builtins, ("rigorous", "reckless"))
    for key, wf in builtins.items():
        assert validate.check_tabs(wf.tabs, wf.start).ok, key
        for mode, graph in wf.tabs.items():
            report = validate.check(graph, wf.start, cat, mode)
            assert [p.as_dict() for p in report.errors] == [], (key, mode)
