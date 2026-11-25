"""The graph engine (engine.py), on small graphs."""

from __future__ import annotations

import pytest

from golem import engine, workflows
from golem.engine import Sub
from golem.store import Step


def graph(blocks: list, md: str = "", sub: bool = False, outputs: dict | None = None) -> dict:
    """A graph from a block list, the way built-ins are written (workflows.build)."""
    return workflows.build(blocks, md, sub, outputs or {})


def agents(*names: str) -> str:
    return "Text every agent gets.\n\n" + "".join(f"## {n}\n\nDo {n}.\n\n" for n in names)


def resolver(subs: dict[str, dict]):
    """Opens containers from sub-workflows by key: {key: graph}."""

    def resolve(key: str) -> Sub:
        if key not in subs:
            raise engine.GraphError(f"no workflow called {key}")
        return Sub(key, key.title(), subs[key])

    return resolve


def flat(g: dict, start: str = "change", subs: dict | None = None) -> dict:
    return engine.flatten(Sub("top", "Top", g), start, resolver(subs or {}))


def walk(snap: dict, ports: dict[str, str] | None = None, vars: dict | None = None, limit: int = 50) -> list[str]:
    """Walk a job's copy to the end, each block leaving by `ports` (else its first output)."""
    vars, trace, at, port = vars or engine.blank_vars(), [], None, None
    for _ in range(limit):
        moved = engine.advance(snap, vars, trace, at, port)
        vars = moved.vars
        trace += [r for r in moved.rows if r.state != "pending"]
        if moved.finished or moved.pending is None:
            return _said(trace)
        step = moved.pending
        step.state, step.started_at = "done", "now"
        trace.append(step)
        at = step.node_id
        port = (ports or {}).get(at, engine.ports(snap["nodes"][at])[0] if engine.ports(snap["nodes"][at]) else None)
        if snap["nodes"][at]["type"] == "end":
            return _said(trace)
    raise AssertionError("still going")


def _said(trace: list[Step]) -> list[str]:
    return [f"{s.node_id}:{s.state}" if s.state != "done" else s.node_id for s in trace]


# --- flattening -------------------------------------------------------------------


SHIP = graph(["push", {"do": "deploy", "fail": "fix"}, {"output": "shipped"}, {"agent": "fix", "turns": 9, "done": "push"}], agents("fix"), sub=True)


def test_containers_open_in_place_with_their_ids_under_the_container():
    g = graph(["setup", {"agent": "build", "turns": 9}, {"use": "ship"}, {"use": "ship"}, "end"], agents("build"), outputs={"ship": ["shipped"]})
    snap = flat(g, subs={"ship": SHIP})
    assert engine.happy_path(snap) == ["setup", "build", "ship/push", "ship/deploy", "ship-2/push", "ship-2/deploy", "end"]
    assert snap["edges"]["ship/deploy"] == {"pass": "ship-2/push", "fail": "ship/fix"}
    assert snap["edges"]["ship/fix"] == {"done": "ship/push"}
    assert snap["levels"]["ship-2"] == {"key": "ship", "name": "Ship"}
    assert snap["nodes"]["ship/fix"]["path"] == "ship" and snap["nodes"]["ship/fix"]["ref"] == "fix"


def test_containers_nest_and_their_outputs_lead_out_through_every_level():
    inner = graph([{"agent": "a", "turns": 1}, {"output": "ok"}], agents("a"), sub=True)
    middle = graph([{"use": "inner"}, {"output": "done"}], sub=True, outputs={"inner": ["ok"]})
    top = graph(["setup", {"use": "middle"}, "end"], outputs={"middle": ["done"]})
    snap = flat(top, subs={"inner": inner, "middle": middle})
    assert engine.happy_path(snap) == ["setup", "middle/inner/a", "end"]


def test_a_workflow_that_contains_itself_is_refused():
    loop = graph([{"use": "loop"}, {"output": "x"}], sub=True, outputs={"loop": ["x"]})
    top = graph(["setup", {"use": "loop"}, "end"], outputs={"loop": ["x"]})
    with pytest.raises(engine.GraphError, match="Loop contains itself"):
        flat(top, subs={"loop": loop})


# --- walking ---------------------------------------------------------------------


def test_a_job_walks_counters_conditions_and_loops_by_the_ports_it_takes():
    g = graph(
        ["setup", {"agent": "review", "turns": 9, "outputs": [{"name": "pass", "about": "good"}, {"name": "fail", "about": "not"}], "fail": "tries"},
         "end", {"counter": "tries"}, {"condition": "again", "all": [["counter", "tries", "<", 2]], "true": "review", "false": "end"}],
        agents("review"),
    )  # fmt: skip
    snap = flat(g)
    assert walk(snap) == ["setup", "review", "end"]
    assert walk(snap, {"review": "fail"}) == ["setup", "review", "tries", "again", "review", "tries", "again", "end"]


def test_a_skipped_block_leaves_by_its_skip_output():
    g = graph(["setup", {"agent": "design", "turns": 9, "optional": True, "contract": "design", "skip_to": "nothing", "nothing": "end"},
               {"agent": "guide", "turns": 9}, "end"], agents("design", "guide"))  # fmt: skip
    snap = flat(g)
    vars = engine.blank_vars()
    vars["nodes"]["design"] = {"skip": True}
    assert walk(snap, vars=vars) == ["setup", "design:skipped", "end"]
    assert engine.predict(snap, vars, [], "setup") == ["design", "end"]  # shown skipped, then by its skip output


def test_what_a_block_found_goes_to_the_next_agent_past_golems_own_blocks():
    g = graph(["setup", "push", {"agent": "fix", "turns": 9}, {"agent": "after", "turns": 9}, "end"], agents("fix", "after"))
    snap = flat(g)
    vars = {**engine.blank_vars(), "context": "CI failed"}
    moved = engine.advance(snap, vars, [], "setup", "next")
    assert moved.pending.node_id == "push" and moved.pending.problem is None
    moved = engine.advance(snap, moved.vars, [], "push", "next")
    assert (moved.pending.name, moved.pending.problem) == ("fix", "CI failed")
    assert engine.advance(snap, moved.vars, [], "fix", "done").vars["context"] == ""  # the agent had it


def test_a_job_that_goes_round_too_often_stops_to_ask():
    g = graph(["setup", {"agent": "a", "turns": 9, "done": "setup"}, "end"], agents("a"))
    snap = flat(g)
    trace = [Step(i, 1, i, "agent", "a", state="done", started_at="now", node_id="a") for i in range(3)]
    moved = engine.advance(snap, engine.blank_vars(), trace, "setup", "next", guard=(3, 100))
    assert moved.vars["guard"]["hit"] == "a" and moved.pending.visit == 4
    vars = moved.vars | {"guard": {"rounds": 1, "hit": ""}}  # Riggy said go on: as many again
    assert engine.advance(snap, vars, trace, "setup", "next", guard=(3, 100)).vars["guard"]["hit"] == ""


def test_counters_and_conditions_in_a_circle_are_stopped():
    g = graph(["setup", {"counter": "n"}, {"condition": "c", "all": [["counter", "n", ">", -1]], "true": "n", "false": "end"}, "end"])
    with pytest.raises(engine.GraphError, match="in a circle"):
        engine.advance(flat(g), engine.blank_vars(), [], "setup", "next")


def test_a_question_ends_quietly_once_it_has_answered():
    g = graph(["refresh", {"agent": "examine", "turns": 9}, "answer", "end"], agents("examine"))
    snap = flat(g, start="look")
    assert engine.advance(snap, engine.blank_vars(), [], "answer", "next").finished
    assert engine.happy_path(snap) == ["refresh", "examine", "answer"]


def test_conditions_read_counters_and_visits():
    node = {"path": "", "rows": [{"of": "visits", "name": "a", "op": ">=", "value": 2}]}
    two = [Step(1, 1, 1, "agent", "a", node_id="a"), Step(2, 1, 2, "agent", "a", node_id="a")]
    assert engine.evaluate(node, {}, two) and not engine.evaluate(node, {}, two[:1])
    node = {"path": "ship", "join": "any", "rows": [{"of": "counter", "name": "fixes", "op": ">=", "value": 4}, {"of": "visits", "name": "a", "op": ">", "value": 9}]}
    assert engine.evaluate(node, {"ship/fixes": 4}, []) and not engine.evaluate(node, {"fixes": 4}, [])  # a container's counters are its own
    assert engine.evaluate(node, {}, [], unknown=True)  # a count not taken yet, predicted
