"""Reading run logs while they're written, and what they show."""

from __future__ import annotations

import asyncio
import gzip
import json
import stat
import sys
from datetime import UTC, datetime, timedelta

from golem.harness import ClaudeHarness
from golem.watch import Steps, Tail

T0 = datetime(2026, 10, 22, 13, 30, tzinfo=UTC)
RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 12,
    "structured_output": {"verdict": "done", "message": "Spec written."},
}


def at(seconds: float, start: datetime = T0) -> str:
    return (start + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")  # as Claude Code writes it


def said(text: str, seconds: float, start: datetime = T0) -> dict:
    content = [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}]
    return {"type": "assistant", "timestamp": at(seconds, start), "message": {"content": content}}


def call(tool_id: str, name: str, seconds: float, start: datetime = T0, **tool_input) -> dict:
    content = [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]
    return {"type": "assistant", "timestamp": at(seconds, start), "message": {"content": content}}


def answer(tool_id: str, output, seconds: float, error: bool = False) -> dict:
    content = [{"type": "tool_result", "tool_use_id": tool_id, "content": output, "is_error": error}]
    return {"type": "user", "timestamp": at(seconds), "message": {"content": content}}


def write(log, *events: dict) -> None:
    for event in events:
        log.write(json.dumps(event) + "\n")
    log.flush()


def test_a_log_can_be_read_while_it_is_written(tmp_path):
    path = tmp_path / "001-spec.jsonl.gz"
    tail = Tail(path)
    assert tail.events() == []
    with gzip.open(path, "wt") as log:
        write(log, {"type": "a"})
        assert tail.events() == [{"type": "a"}]
        log.write(json.dumps({"type": "b"}) + '\n{"type": ')
        log.flush()
        assert tail.events() == [{"type": "b"}]
        log.write('"c"}\n')
    assert tail.events() == [{"type": "c"}]
    assert tail.events() == []


def test_the_harness_log_can_be_read_while_claude_runs(tmp_path):
    go = tmp_path / "go"
    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\nimport json, os, sys, time\nsys.stdin.read()\n"
        "print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's'}), flush=True)\n"
        f"while not os.path.exists({str(go)!r}):\n    time.sleep(0.01)\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,"
        " 'structured_output': {'verdict': 'done', 'message': 'ok'}}))\n"
    )
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log_path = tmp_path / "001-build.jsonl.gz"

    async def scenario():
        harness = ClaudeHarness(str(binary))
        run = asyncio.create_task(
            harness.run(cwd=tmp_path, prompt="p", model="opus", max_turns=5, log_path=log_path, env={})
        )
        tail, seen = Tail(log_path), []
        for _ in range(500):
            seen += tail.events()
            if seen:
                break
            await asyncio.sleep(0.01)
        go.touch()
        return seen, await run

    seen, result = asyncio.run(scenario())
    assert [e.get("subtype") for e in seen] == ["init"]
    assert result.outcome == "verdict"


def test_each_step_gets_a_line():
    steps = Steps(T0)
    subagent = {"parent_tool_use_id": "t2"}
    lines = steps.lines(
        [
            {"type": "system", "subtype": "init", "model": "claude-opus-5-5"},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50},
            said("Reading the brief.\nThen the spec.", 3),
            call("t1", "Bash", 65, command="npm test", description="Run the tests"),
            answer("t1", "> test\n\n42 passed", 70),
            call("t2", "Task", 80, description="Review the spec", prompt="Read SPEC.md and …"),
            call("t3", "Read", 81, file_path="/workspace/trees/SPEC.md") | subagent,
            answer("t3", [{"type": "text", "text": "# Spec"}], 82) | subagent,
            answer("t2", "", 90, error=True),
            {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}},
            {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning", "rateLimitType": "five_hour"}},
            call("t4", "StructuredOutput", 95, verdict="done", message="Spec written."),
            answer("t4", "Structured output provided successfully", 95),
            RESULT,
        ]
    )
    assert lines == [
        "          started on claude-opus-5-5",
        "    0:03  says: Reading the brief. Then the spec.",
        "    1:05  → Bash npm test",
        "    1:10  ← > test … 42 passed (2 lines)",
        "    1:20  → Task Review the spec",
        "    1:21  ┆ → Read /workspace/trees/SPEC.md",
        "    1:22  ┆ ← # Spec",
        "    1:30  ✗ (no output)",
        "          Claude usage: allowed_warning (five_hour)",
        "          ■ finished after 12 turns: done: Spec written.",
    ]
    assert steps.ended


def test_steps_know_what_the_worker_is_on():
    steps = Steps(T0)
    steps.lines([call("t1", "Bash", 60, command="npm run dev")])
    assert steps.doing(T0 + timedelta(minutes=4)) == "running Bash npm run dev for 3m"
    steps.lines([answer("t1", "listening on :3000", 61)])
    assert steps.doing(T0 + timedelta(minutes=4)) == "thinking for 2m"
