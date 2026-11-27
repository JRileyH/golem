"""OpenCode as a worker (opencode.py): its events logged as Claude Code's, its verdict read from its reply."""

import asyncio
import gzip
import json
import os
import stat
import sys

from golem import compare, glance
from golem.harness import VERDICT_SCHEMA
from golem.opencode import OpenCodeHarness, fitted, last_object
from golem.watch import Steps, Tail

# A stand-in for `opencode run`: it notes how it was called, then says what FAKE_PLAN has it say.
FAKE_OPENCODE = """#!{python}
import json, os, sys
prompt = sys.stdin.read()
with open(os.environ["FAKE_RECORD"], "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "prompt": prompt, "config": json.loads(os.environ["OPENCODE_CONFIG_CONTENT"]),
                        "data": os.environ["XDG_DATA_HOME"], "claude": os.environ.get("OPENCODE_DISABLE_CLAUDE_CODE")}}) + "\\n")
plan, again = os.environ["FAKE_PLAN"], "--session" in sys.argv
def say(kind, **part):
    print(json.dumps({{"type": kind, "timestamp": 1795808580000 + part.pop("at", 0), "sessionID": "ses_1", "part": part}}))
def step(cost=0.0, inp=100, out=20, read=1000, at=0):
    say("step_finish", reason="stop", messageID="msg", cost=cost, at=at,
        tokens={{"input": inp, "output": out, "reasoning": 5, "cache": {{"read": read, "write": 10}}}})
verdict = '{{"verdict": "done", "message": "tests pass", "extra": 1}}'
if plan == "limit":
    print(json.dumps({{"type": "error", "sessionID": "ses_1", "error": {{"name": "APIError", "data": {{"message": "Free usage limit reached"}}}}}}))
    sys.exit(1)
if plan == "ok" or (plan == "again" and again):
    say("tool_use", tool="bash", callID="c1", messageID="msg",
        state={{"status": "completed", "input": {{"command": "bin/test"}}, "output": "3 passed", "metadata": {{"exit": 0}},
               "time": {{"start": 1795808581000, "end": 1795808701000}}}})
    say("tool_use", tool="read", callID="c2", messageID="msg",
        state={{"status": "error", "input": {{"filePath": "/x/nope.py"}}, "error": "File not found",
               "time": {{"start": 1795808702000, "end": 1795808702500}}}})
    step(cost=0.25, at=123000)
    say("text", messageID="msg2", text="All good.\\n\\n```json\\n" + verdict + "\\n```", at=125000)
    step(cost=0.05, at=126000)
elif plan == "again":
    say("text", messageID="msg", text="I fixed it.")
    step()
elif plan == "turns":
    for _ in range(3):
        step()
    say("text", messageID="msg", text='Maximum steps reached. {{"verdict": "incomplete", "message": "half"}}')
"""


def harness(tmp_path, plan: str, **run):
    binary = tmp_path / "opencode"
    binary.write_text(FAKE_OPENCODE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    record, log_path = tmp_path / "calls.jsonl", tmp_path / "logs" / "001-spec.jsonl.gz"
    env = {"PATH": os.environ["PATH"], "GOLEM_TMP": str(tmp_path / "tmp"), "FAKE_RECORD": str(record), "FAKE_PLAN": plan}
    args = {"cwd": tmp_path, "prompt": "build it", "model": "opencode/mimo-free", "max_turns": 3, "log_path": log_path, "env": env, **run}
    result = asyncio.run(OpenCodeHarness(str(binary)).run(**args))
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    with gzip.open(log_path, "rt") as f:
        events = [json.loads(line) for line in f]
    return result, calls, events, log_path


def test_a_run_is_logged_as_claude_codes_so_glance_tail_and_compare_read_it(tmp_path):
    r, calls, events, log_path = harness(tmp_path, "ok", effort="high", resume=None)

    assert (r.outcome, r.session_id) == ("verdict", "ses_1")
    assert r.verdict == {"verdict": "done", "message": "tests pass"}  # what the schema doesn't know is dropped
    assert r.usage["num_turns"] == 2 and r.usage["cost_usd"] == 0.3 and r.usage["output_tokens"] == 50
    (call,) = calls  # the verdict was there: no second call
    assert call["argv"] == ["run", "--format", "json", "--auto", "-m", "opencode/mimo-free", "--variant", "high"]
    assert call["prompt"].startswith("build it\n\n## Your verdict") and json.dumps(VERDICT_SCHEMA) in call["prompt"]
    assert '{"verdict": "done", "message": "One or two sentences for Riggy, plain text", "link": "URL the message is about, if any"}' in call["prompt"]
    assert call["config"]["agent"]["build"]["steps"] == 3 and call["config"]["instructions"] == ["CLAUDE.md"]
    assert call["data"] == str(tmp_path / "tmp" / "opencode" / "data") and call["claude"] == "1"
    assert [e["type"] for e in events] == ["system", "assistant", "user", "assistant", "user", "assistant", "assistant", "assistant", "result"]

    g = glance.of(events)
    assert (g.test_runs, g.tests) == (1, 120.0)
    assert g.slowest == "bin/test" and g.slowest_seconds == 120.0
    lines = Steps(glance._time(events[0]["timestamp"])).lines(Tail(log_path).events())
    assert any("→ Bash bin/test" in line for line in lines) and any("✗ File not found" in line for line in lines)
    assert any("→ Read /x/nope.py" in line for line in lines)  # filePath read as file_path
    assert lines[-1].endswith("■ finished after 2 turns: done: tests pass")
    log = compare.read_log(log_path)
    assert (log.turns, log.fresh_in, log.cache_read, log.cache_write, log.tokens_out) == (2, 200, 2000, 20, 50)
    assert log.calls == {"Bash": 1, "Read": 1} and log.failed == 1


def test_a_reply_without_its_verdict_is_asked_once_more_tools_off(tmp_path):
    r, calls, events, _ = harness(tmp_path, "again")

    assert r.outcome == "verdict" and r.verdict["message"] == "tests pass"
    assert len(calls) == 2
    assert calls[1]["argv"][-2:] == ["--session", "ses_1"] and calls[1]["config"]["agent"]["build"]["steps"] == 1
    assert calls[1]["prompt"].startswith("Your reply didn't end with your verdict")
    assert events[-1]["num_turns"] == 3  # both calls' steps count


def test_its_last_step_spent_without_a_verdict_is_running_out_of_turns(tmp_path):
    r, calls, _, _ = harness(tmp_path, "turns")

    assert (r.outcome, r.session_id) == ("max_turns", "ses_1")  # golem's wrapup resumes the session
    assert len(calls) == 1  # no steps left to ask again with


def test_a_free_models_limit_parks_the_job_like_a_usage_limit(tmp_path):
    r, _, events, _ = harness(tmp_path, "limit")

    assert r.outcome == "limit" and "Free usage limit" in r.error
    assert events[-1]["subtype"] == "error_during_execution"


def test_a_custom_schema_takes_its_own_fields_and_no_verdict_is_needed(tmp_path):
    schema = {"type": "object", "properties": {"finished": {"type": "boolean"}, "message": {"type": "string"}},
              "required": ["finished", "message"], "additionalProperties": False}  # fmt: skip
    assert fitted({"finished": True, "message": "ok"}, schema, expected=False) == {"finished": True, "message": "ok"}
    assert fitted({"message": "ok"}, schema, expected=False) is None  # what's required is missing
    outputs = {**VERDICT_SCHEMA, "properties": {**VERDICT_SCHEMA["properties"], "verdict": {"enum": ["pass", "fail", "blocked"]}}}
    assert fitted({"verdict": "pass", "message": "m"}, outputs, expected=False)["verdict"] == "pass"
    assert fitted({"verdict": "done", "message": "m"}, outputs, expected=False) is None  # not one of its outputs
    shaped = {"type": "object", "properties": {"verdict": "done", "message": "m"}, "required": ["verdict", "message"]}
    assert fitted(shaped, VERDICT_SCHEMA, expected=True) == {"verdict": "done", "message": "m"}  # a weak model's answer in the schema's shape


def test_the_object_a_reply_ends_with():
    assert last_object('Done.\n{"verdict": "done", "message": "a {b}"}') == {"verdict": "done", "message": "a {b}"}
    assert last_object('ok\n```json\n{"a": {"b": 1}}\n```\n') == {"a": {"b": 1}}  # the outer one, fenced
    assert last_object('{"verdict": "done"} and then more words') is None
    assert last_object("no json here") is None


def test_a_job_in_an_opencode_mode_runs_every_agent_step_on_opencode(golem):
    from golem.harness import FakeHarness

    async def go():
        opencode = FakeHarness()
        golem.scheduler.agents = {"opencode": opencode}
        golem.harness.script = [{"name": "Tree Cards", "slug": "tree-cards", "summary": "Tree flashcards.", "title": "Tree Cards"}]
        job = await golem.new_project("a flashcard game for trees", mode="opencode-free")
        await golem.until("spec")
        before = len(golem.harness.calls)
        await golem.step()
        assert len(golem.harness.calls) == before  # not Claude Code
        assert opencode.calls[-1].model == "opencode/fake-free"  # whichever free model OpenCode offers
        assert golem.store.runs(job.id)[-1]["model"] == "opencode/fake-free"

        golem.scheduler.agents = {}  # a golem without OpenCode: the step fails rather than run on Claude Code
        await golem.until("scaffold")
        await golem.step()
        assert any("golem has no opencode worker" in (s.error or "") for s in golem.store.steps(job.id))

    asyncio.run(go())
