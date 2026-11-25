"""Workflows: golem's built-ins (workflows/), read into graphs, and the way each goes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from golem import engine, triage, workflows
from golem.store import Store

REPO = Path(__file__).resolve().parents[2]
SHIP = ["push", "pr", "checks", "merge", "deploy"]


def ship(at: str = "ship") -> list[str]:
    return [f"{at}/{s}" for s in SHIP]


# Each built-in's usual way through: every block's first output, from Start.
HAPPY = {
    "adopt": ["vet", "setup", "adopt", *ship(), "end"],
    "change": ["setup", "change", "review", "docs", *ship(), "end"],
    "design": ["setup", "design_round/design", "design_round/choose", "guide", "apply", "review", "docs", *ship(), "end"],
    "options": ["setup", "options", "choose", "change", "review", "docs", *ship(), "end"],
    "proposals": ["refresh", "propose", "proposals"],
    "question": ["refresh", "examine", "answer"],
    "web_app": ["setup", "spec", "scaffold", *ship(), "mvp", *ship("ship-2"), "checkin", "design", "choose", "guide", "apply",
                *ship("ship-3"), "buildout", "review", "docs", *ship("ship-4"), "end"],
}  # fmt: skip
BUILDS = {"scaffold", "mvp", "buildout", "change", "guide", "apply", "adopt"}  # the blocks that take a build turn


@pytest.fixture(scope="module")
def builtins():
    return workflows.load_all(REPO / "workflows")


def snapshot(builtins, key: str) -> dict:
    return engine.flatten(workflows.top(builtins, key), builtins[key].start, workflows.resolver(builtins))


# What a job of each start type can't do, and how it starts: golem's guarantees, held by every
# workflow it runs (a job that changes a project starts with setup; one that only looks never
# pushes), checked here since golem's workflows are its own files.
BANNED = {
    "new": ("refresh", "vet", "answer", "proposals"),
    "change": ("refresh", "vet", "answer", "proposals"),
    "adopt": ("refresh", "answer", "proposals"),
    "look": ("setup", "vet", *engine.SHIPS, "proposals"),
    "propose": ("setup", "vet", *engine.SHIPS, "answer", "checkin", "choose"),
    "triage": ("setup", "vet", "refresh", *engine.SHIPS, "answer", "proposals", "checkin", "choose"),
}
STARTS_WITH = {"new": ["setup"], "change": ["setup"], "adopt": ["vet", "setup"], "look": ["refresh"], "propose": ["refresh"], "triage": []}
ENDS_WITH = {"look": "answer", "propose": "proposals"}  # the block that tells Riggy, on every way through


def _guarantees(snap: dict, start: str) -> None:
    nodes, edges = snap["nodes"], snap["edges"]
    path = engine.happy_path(snap)
    first = STARTS_WITH[start]
    assert [nodes[n]["type"] for n in path[: len(first)]] == first
    for nid, n in nodes.items():
        assert n["type"] not in BANNED[start], nid
        assert n["type"] not in first or nid in path[: len(first)], f"{nid} only comes first"
        for port in engine.ports(n):
            assert port in edges[nid], f"{nid}'s {port} isn't wired"
    if last := ENDS_WITH.get(start):  # no way to End that doesn't pass it
        seen, todo = set(), [snap["start"]]
        while todo:
            at = todo.pop()
            if at in seen or nodes[at]["type"] == last:
                continue
            seen.add(at)
            assert nodes[at]["type"] != "end", f"a way through skips {last}"
            todo += list(edges.get(at, {}).values())


@pytest.mark.parametrize("key", sorted(HAPPY))
def test_every_builtin_keeps_golems_guarantees_for_its_start(builtins, key):
    _guarantees(snapshot(builtins, key), builtins[key].start)
    if builtins[key].start == "propose":
        assert sum(1 for n in snapshot(builtins, key)["nodes"].values() if n.get("contract") == "propose") == 1


def test_triage_keeps_them_too():
    _guarantees(triage.snapshot(), "triage")


def test_every_workflow_file_is_a_builtin_with_a_fallback_for_each_start(builtins):
    assert set(builtins) == {p.stem for p in (REPO / "workflows").glob("*.yml")}
    for start in ("new", "change", "look", "propose", "adopt"):
        assert [wf.key for wf in builtins.values() if wf.start == start and wf.fallback], start


@pytest.mark.parametrize("key", sorted(HAPPY))
def test_each_builtin_goes_its_usual_way_taking_its_turns(builtins, key):
    snap = snapshot(builtins, key)
    path = engine.happy_path(snap)
    assert path == HAPPY[key]
    for nid in path:
        want = "ship" if nid.split("/")[-1] in SHIP else "build" if nid in BUILDS else "none"
        assert engine.turn_of(snap["nodes"][nid]) == want, nid
    # The web builds have the quality bar; a look doesn't build.
    assert ("# Quality bar" in snap["intros"][""]) == (key not in ("question", "proposals"))


@pytest.mark.parametrize("key", sorted(HAPPY))
def test_failures_go_to_a_fix_and_back_to_the_last_push(builtins, key):
    """A fix, then the steps from the last push again; after four, a hint asked of Riggy."""
    snap = snapshot(builtins, key)
    nodes, edges = snap["nodes"], snap["edges"]
    path = engine.happy_path(snap)
    for i, nid in enumerate(path):
        if nodes[nid]["type"] not in ("checks", "deploy"):
            continue
        at = edges[nid]["fail"]
        assert nodes[at]["type"] == "condition" and nodes[edges[at]["true"]]["type"] == "checkin"
        at = edges[at]["false"]
        assert nodes[at].get("name") == "fix"
        back = edges[at]["done"]
        while nodes[back]["type"] in engine.CONTROL:
            back = edges[back]["next"]
        assert back == [n for n in path[: i + 1] if nodes[n]["type"] == "push"][-1]


@pytest.mark.parametrize("key", sorted(HAPPY))
def test_replies_picks_and_empty_design_rounds_go_where_golem_sent_them(builtins, key):
    snap = snapshot(builtins, key)
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


# --- the library: what jobs copy -------------------------------------------------------


@pytest.fixture
def library(tmp_path):
    folder = tmp_path / "workflows"
    shutil.copytree(REPO / "workflows", folder)
    return workflows.Library(Store(tmp_path / "golem.db"), folder, "secret")


def test_the_library_is_golems_files(library):
    assert library.fallback("new").key == "web_app" and library.fallback("change").key == "change"
    assert {w.key for w in library.choices(("change", "look"))} == {"change", "options", "design", "question"}
    yml = library.folder / "change.yml"
    yml.write_text(yml.read_text().replace("name: Change", "name: Make a change"))
    assert library.get("change").name == "Make a change"  # read again once its files change


def test_a_job_copies_its_workflow_with_its_stage_blocks_texts(library):
    snap = library.pin(library.get("change"), "change")
    assert snap["start"] == "setup" and snap["levels"]["ship"] == {"key": "ship", "name": "Ship"}
    change = snap["nodes"]["change"]
    assert change["parts"] and "Make the change" in change["instructions"]


def test_a_finished_job_keeps_its_copy_as_its_record(library):
    store = library.blocks.store
    store.add_project("trees", "Trees", "s")
    job = store.add_job("trees", "change", "r", "feature")
    store.update_job(job.id, graph='{"v": 1}', vars={"counters": {"fixes": 1}})
    assert store.job(job.id).graph and json.loads(store.job(job.id).vars)["counters"] == {"fixes": 1}
    store.update_job(job.id, state="done")
    assert store.job(job.id).graph == '{"v": 1}'
