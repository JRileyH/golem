"""The graph engine (engine.py) and what makes a workflow valid (validate.py), on small graphs."""

from __future__ import annotations

import pytest

from golem import engine, validate, workflows
from golem.engine import Sub
from golem.store import Step


def graph(blocks: list, md: str = "", sub: bool = False, outputs: dict | None = None) -> dict:
    """A graph from a block list, the way built-ins are written (workflows.build)."""
    return workflows.build(blocks, md, sub, outputs or {})


def agents(*names: str) -> str:
    return "Text every agent gets.\n\n" + "".join(f"## {n}\n\nDo {n}.\n\n" for n in names)


def catalog(subs: dict[str, dict[str, dict]], modes=("rigorous", "reckless")) -> validate.Catalog:
    """A catalog of sub-workflows: {key: {tab: graph}}."""

    def resolve(key: str, tab: str, mode: str) -> Sub:
        if key not in subs:
            raise engine.GraphError(f"no workflow called {key}")
        want = mode if tab in ("auto", "") else ("" if tab == "default" else tab)
        have = want if want in subs[key] else ""
        return Sub(key, key.title(), have, subs[key][have])

    info = {k: validate.SubInfo(k.title(), tuple(engine.outputs_of(t[""])), tuple(m for m in t if m)) for k, t in subs.items()}
    return validate.Catalog(info, resolve, modes)


def flat(g: dict, start: str = "change", mode: str = "", subs: dict | None = None) -> dict:
    return engine.flatten(Sub("top", "Top", mode, g), start, mode, catalog(subs or {}).resolve)


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
SHIP_FAST = graph(["push-main", {"do": "deploy", "fail": "out-shipped"}, {"output": "shipped"}], sub=True)


def test_containers_open_in_place_with_their_ids_under_the_container():
    g = graph(["setup", {"agent": "build", "turns": 9}, {"use": "ship"}, {"use": "ship"}, "end"], agents("build"), outputs={"ship": ["shipped"]})
    snap = flat(g, subs={"ship": {"": SHIP}})
    assert engine.happy_path(snap) == ["setup", "build", "ship/push", "ship/deploy", "ship-2/push", "ship-2/deploy", "end"]
    assert snap["edges"]["ship/deploy"] == {"pass": "ship-2/push", "fail": "ship/fix"}
    assert snap["edges"]["ship/fix"] == {"done": "ship/push"}
    assert {k: v for k, v in snap["levels"]["ship-2"].items() if k != "graph"} == {"key": "ship", "name": "Ship", "tab": ""}
    assert snap["nodes"]["ship/fix"]["path"] == "ship" and snap["nodes"]["ship/fix"]["ref"] == "fix"


def test_a_container_opens_the_tab_for_the_jobs_mode_unless_it_says_which():
    subs = {"ship": {"": SHIP, "reckless": SHIP_FAST}}
    auto = graph(["setup", {"use": "ship"}, "end"], outputs={"ship": ["shipped"]})
    pinned = graph(["setup", {"use": "ship", "tab": "default"}, "end"], outputs={"ship": ["shipped"]})
    assert "ship/push-main" in flat(auto, mode="reckless", subs=subs)["nodes"]
    assert "ship/push" in flat(auto, mode="rigorous", subs=subs)["nodes"]  # no rigorous tab: the default
    assert "ship/push" in flat(pinned, mode="reckless", subs=subs)["nodes"]


def test_containers_nest_and_their_outputs_lead_out_through_every_level():
    inner = graph([{"agent": "a", "turns": 1}, {"output": "ok"}], agents("a"), sub=True)
    middle = graph([{"use": "inner"}, {"output": "done"}], sub=True, outputs={"inner": ["ok"]})
    top = graph(["setup", {"use": "middle"}, "end"], outputs={"middle": ["done"]})
    snap = flat(top, subs={"inner": {"": inner}, "middle": {"": middle}})
    assert engine.happy_path(snap) == ["setup", "middle/inner/a", "end"]


def test_a_workflow_that_contains_itself_is_refused():
    loop = graph([{"use": "loop"}, {"output": "x"}], sub=True, outputs={"loop": ["x"]})
    top = graph(["setup", {"use": "loop"}, "end"], outputs={"loop": ["x"]})
    with pytest.raises(engine.GraphError, match="Loop contains itself"):
        flat(top, subs={"loop": {"": loop}})


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


def test_conditions_read_the_mode_and_visits():
    g = graph(["setup", {"condition": "careful", "all": [["mode", "", "==", "rigorous"]], "true": "twice", "false": "end"},
               {"agent": "twice", "turns": 9}, "end"], agents("twice"))  # fmt: skip
    assert walk(flat(g, mode="rigorous")) == ["setup", "careful", "twice", "end"]
    assert walk(flat(g)) == ["setup", "careful", "end"]
    node = {"path": "", "rows": [{"of": "visits", "name": "a", "op": ">=", "value": 2}]}
    two = [Step(1, 1, 1, "agent", "a", node_id="a"), Step(2, 1, 2, "agent", "a", node_id="a")]
    assert engine.evaluate(node, {}, two, "") and not engine.evaluate(node, {}, two[:1], "")


# --- validation: one test per rule, with its exact words ------------------------------

OK = graph(["setup", {"agent": "build", "turns": 9}, "end"], agents("build"))


def problems(g: dict, start: str = "change", subs: dict | None = None, mode: str = "") -> list[str]:
    return [p.message for p in validate.check(g, start, catalog(subs or {}), mode).errors]


def edit(g: dict, nid: str, **changes) -> dict:
    g = {**g, "nodes": [dict(n, **changes) if n["id"] == nid else n for n in g["nodes"]]}
    return g


def unwire(g: dict, src: str, port: str) -> dict:
    return {**g, "edges": [e for e in g["edges"] if not (e["from"] == src and e["port"] == port)]}


def test_a_simple_workflow_is_valid():
    assert problems(OK) == []


@pytest.mark.parametrize(
    "g, message",
    [
        (unwire(OK, "build", "done"), "build's done output isn't wired to anything"),
        ({**OK, "nodes": [n for n in OK["nodes"] if n["type"] != "end"], "edges": [e for e in OK["edges"] if e["to"] != "end"]},
         "A workflow needs at least one End block: every path has to end"),
        ({**OK, "nodes": [*OK["nodes"], {"id": "start2", "type": "start"}]}, "A workflow has exactly one Start block"),
        ({**OK, "nodes": [*OK["nodes"], {"id": "lost", "type": "push"}], "edges": [*OK["edges"], {"from": "lost", "port": "next", "to": "end"}]},
         "Nothing leads to push"),
        (edit(OK, "build", instructions=""), "build needs instructions: what the agent should do"),
        (edit(OK, "build", turns=0), "Turns is a whole number from 1 to 1000"),
        (edit(OK, "build", name=""), "An agent block needs a name"),
        (edit(OK, "setup", optional=True), "Only agent, script, check-in and choose blocks can be optional, not setup"),
        (edit(OK, "build", turn="sometimes"), "Turn is one of build, ship, none"),
        ({**OK, "edges": [*OK["edges"], {"from": "build", "port": "nope", "to": "end"}]}, "build has no nope output any more: rewire it"),
        ({**OK, "edges": [*OK["edges"], {"from": "setup", "port": "next", "to": "end"}]}, "setup's next output is wired twice"),
        ({**OK, "edges": [*OK["edges"][:-1], {"from": "build", "port": "done", "to": "start"}]}, "Nothing can be wired into Start"),
        (edit(OK, "build", outputs=[{"name": "pass", "about": ""}, {"name": "fail", "about": "broken"}]),
         "Say when to take pass: with several outputs, that's how the choice is made"),
        (edit(OK, "build", outputs=[{"name": "blocked", "about": "x"}]),
         "blocked is golem's own (the agent asks Riggy and carries on): call the output something else"),
    ],
)
def test_each_rule_says_what_is_wrong(g, message):
    assert message in problems(g)


def test_a_way_round_with_no_end_is_refused():
    g = graph(["setup", {"agent": "a", "turns": 9, "done": "b"}, "end", {"agent": "b", "turns": 9, "done": "a"}], agents("a", "b"))
    assert "From a there's no way to End: it would go round forever" in problems(g)


def test_start_type_rules_hold_inside_containers():
    sneaky = graph(["refresh", {"output": "x"}], sub=True)
    g = graph(["setup", {"use": "sneaky"}, "end"], outputs={"sneaky": ["x"]})
    assert "A workflow for a change can't have a refresh block" in problems(g, subs={"sneaky": {"": sneaky}})


@pytest.mark.parametrize(
    "start, blocks, message",
    [
        ("change", [{"agent": "build", "turns": 9}, "end"], "A workflow for a change starts with setup"),
        ("new", ["setup", {"agent": "build", "turns": 9}, "setup", "end"], "setup only comes first"),
        ("adopt", ["setup", {"agent": "build", "turns": 9}, "end"], "A workflow for an adoption starts with vet, then setup"),
        ("look", ["refresh", {"agent": "build", "turns": 9}, "push", "answer", "end"], "A workflow for a question can't have a push block"),
        ("look", ["refresh", {"agent": "build", "turns": 9}, "end"], "Every way through a workflow for a question passes answer: that's what tells Riggy"),
        ("propose", ["refresh", {"agent": "build", "turns": 9}, "proposals", "end"], "A proposals workflow has exactly one agent block with the propose contract"),
    ],
)
def test_each_start_type_has_its_rules(start, blocks, message):
    assert message in problems(graph(blocks, agents("build")), start)


def test_a_choose_needs_options_before_it_and_a_reply_an_agent_after():
    g = graph(["setup", {"choose": "choose", "more": "setup"}, {"agent": "build", "turns": 9}, "end"], agents("build"))
    assert "A choose block needs an options or design block before it: that's what it shows" in problems(g)
    g = graph(["setup", {"agent": "build", "turns": 9}, {"checkin": "checkin", "reply_to": "build"}, "end"], agents("build"))
    assert "The reply goes to an agent block after this one, and build doesn't come after it" in problems(g)


def test_containers_must_open_a_sub_workflow_tab_that_exists():
    g = graph(["setup", {"use": "ship", "tab": "fancy"}, "end"], outputs={"ship": ["shipped"]})
    assert "Ship has no fancy tab" in problems(g, subs={"ship": {"": SHIP}})
    gone = graph(["setup", {"use": "gone"}, "end"], outputs={"gone": ["x"]})
    assert "This container's sub-workflow isn't there any more" in problems(gone)


def test_every_tab_of_a_sub_workflow_has_the_same_outputs():
    other = graph(["push", {"output": "elsewhere"}], sub=True)
    assert validate.check_tabs({"": SHIP, "reckless": SHIP_FAST}, "sub").ok
    assert not validate.check_tabs({"": SHIP, "reckless": other}, "sub").ok


def test_sub_workflows_use_input_and_output():
    assert "Start blocks are for sub-workflows: use Input and Output here" not in problems(SHIP, "sub")
    assert problems(SHIP, "sub") == []
    assert "A sub-workflow needs at least one Output block: every path has to end" in problems(graph(["push", "end"], sub=True), "sub")


def test_loops_with_nothing_to_stop_them_are_warned_about():
    g = graph(["setup", {"agent": "a", "turns": 9, "done": "b"}, "end", {"agent": "b", "turns": 9, "outputs": [{"name": "again", "about": "x"}, {"name": "out", "about": "y"}], "again": "a", "out": "end"}], agents("a", "b"))  # fmt: skip
    report = validate.check(g, "change", catalog({}))
    assert report.ok and "This loop has no condition or check-in to stop it: golem's loop guard will" in [w.message for w in report.warnings]


def test_problems_inside_a_container_point_at_the_container():
    g = graph(["setup", {"use": "sneaky"}, "end"], outputs={"sneaky": ["x"]})
    report = validate.check(g, "change", catalog({"sneaky": {"": graph(["refresh", {"output": "x"}], sub=True)}}))
    assert [p.node for p in report.errors] == ["sneaky"]
