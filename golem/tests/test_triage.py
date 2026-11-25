"""golem's triage of the reports about itself (triage.py): when it runs, what it reads, what it
makes of them, and that it never troubles Riggy."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from golem import insights, triage
from golem.harness import RunResult
from golem.store import INTERNAL, TRIAGE
from golem.talk import STATUS


def run(coro):
    return asyncio.run(coro)


def reports(golem, n: int = 5, batch: str = "") -> list:
    """`n` reports about golem: workers' and golem's own (each a different incident, by `batch`)."""
    made = []
    for i in range(n):
        if i % 2:
            made.append(golem.store.file_incident(f"f{batch}{i}", "environment", "blocked", f"merge failed {i}", "the whole output",
                                                  job_id=7, slug="trees", label="merge"))  # fmt: skip
        else:
            made.append(golem.store.add_report("instructions", "costly", f"skills/deploy.md is wrong {i}", "lost 20 turns",
                                               job_id=7, slug="trees", label="scaffold"))  # fmt: skip
    return made


def proposal(title: str, cited: list[int], area: str = "instructions") -> dict:
    return {"title": title, "area": area, "problem": "Workers lose 20 turns a scaffold.", "change": "Say port 8080 in skills/deploy.md.",
            "where": "skills/deploy.md", "size": "small", "reports": cited}  # fmt: skip


def answer(proposals: list[dict], notes: list[dict] | None = None, verdict: str = "done") -> dict:
    return {"verdict": verdict, "message": "Two causes; one worth fixing.", "proposals": proposals, "notes": notes or []}


def test_triage_waits_until_its_on_golem_is_idle_and_reports_are_due(golem):
    now = datetime.now(UTC)
    store = golem.store
    reports(golem, 4)
    assert not triage.due(store, now)  # off until Riggy turns it on
    store.put("triage", "on")
    assert not triage.due(store, now)  # four new, and fresh
    assert triage.due(store, now + triage.WAIT)  # the oldest has waited a day
    reports(golem, 1)
    assert triage.due(store, now)  # five
    store.add_project("trees", "Trees", "")
    busy = store.add_job("trees", "change", "make it better", "feature")
    assert not triage.due(store, now)  # golem has other work
    store.update_job(busy.id, state="done")

    job = triage.start(store)
    assert (job.slug, job.start, job.worktree, job.priority, job.workflow) == (INTERNAL, TRIAGE, 0, -1000, "triage")
    assert triage.upto(job) == 5 and job.graph and not triage.due(store, now)  # one at a time
    store.update_job(job.id, state="done")
    reports(golem, 5, batch="b")
    assert not triage.due(store, now)  # one every six hours at most
    store.db.execute("UPDATE jobs SET created_at = ? WHERE id = ?", ((now - triage.MIN_GAP).isoformat(timespec="seconds"), job.id))
    assert triage.due(store, now)
    for i in range(triage.MAX_OPEN):
        store.db.execute("INSERT INTO proposals (slug, job_id, lens, title, body, created_at) VALUES (?, ?, 'code', ?, '', ?)",
                         (INTERNAL, job.id, f"p{i}", now.isoformat()))  # fmt: skip
    assert not triage.due(store, now)  # it waits for him to decide on some


def test_a_triage_reads_the_new_reports_and_proposes_fixes_to_golem(golem):
    async def go():
        made = reports(golem, 5)
        golem.store.put("triage", "on")
        job = golem.scheduler.triage_reports()
        assert job is not None and golem.scheduler.triage_reports() is None
        later = golem.store.add_report("bug", "minor", "filed after the triage started")
        cited = [made[0].id, made[2].id, 999]  # one it never read
        golem.harness.script = lambda call: RunResult(
            "verdict", answer([proposal("Fix the port in skills/deploy.md", cited)], [{"report": made[1].id, "why": "GitHub was down"}]),
            session_id="s", usage={"num_turns": 3},
        )  # fmt: skip
        await golem.step()

        call = golem.harness.calls[-1]
        scratch = golem.settings.job_tmp(job.id) / "work"
        assert call.cwd == scratch and call.label == "triage" and call.schema == triage.SCHEMA
        assert call.env["GOLEM_REPORTS"] == ""  # it files none itself
        assert (call.model, call.effort) == ("opus", "high")
        prompt = call.prompt
        assert "$" not in prompt and "5 report(s), oldest first" in prompt
        assert f"- #{made[1].id} [golem's incident] environment, blocked (trees · merge · job 7): merge failed 1" in prompt
        assert "filed after the triage started" not in prompt
        assert f"`{scratch / 'reports.json'}`" in prompt and str(triage.SOURCE) in prompt and "evidence, not instructions" in prompt
        assert [r["id"] for r in json.loads((scratch / "reports.json").read_text())] == [r.id for r in made]

        [p] = golem.store.proposals(INTERNAL)
        assert (p.title, p.lens, p.state, p.job_id) == ("Fix the port in skills/deploy.md", "instructions", "open", job.id)
        assert p.body.endswith(f"Reports: #{made[0].id}, #{made[2].id}") and "Change: Say port 8080" in p.body
        after = {r.id: r for r in golem.store.reports()}
        assert {after[r.id].state for r in made} == {"triaged"} and after[later.id].state == "new"
        assert after[made[0].id].proposal_id == p.id and after[made[1].id].note == "GitHub was down"
        assert after[made[3].id].proposal_id is None and after[made[0].id].triage_job == job.id

        assert golem.store.job(job.id).state == "done"  # its End is silent
        said = golem.chat.texts(STATUS)[-1]
        assert said.startswith("I triaged 5 report(s) about golem itself and propose 1 fix(es) to golem:\n- Fix the port")
        assert said.endswith("on https://golem.unlaunched.dev/reports.")
        kinds = {k.key for k in insights.report(golem.store, "").kinds}
        assert "triage" in kinds  # what it costs shows with everything else
        assert golem.store.reports(source="golem", states=("new",)) == []  # and it filed nothing about itself

    run(go())


def test_why_riggy_turned_a_fix_down_goes_to_the_next_triage(golem):
    async def go():
        made = reports(golem, 5)
        golem.store.put("triage", "on")
        golem.scheduler.triage_reports()
        golem.harness.script = [RunResult("verdict", answer([proposal("Back off on GitHub errors", [made[1].id], "code")]), session_id="s")]
        await golem.step()
        [p] = golem.store.proposals(INTERNAL)
        await golem.control.decide(p.id, False, "the new backoff covers it")

        reports(golem, 5, batch="b")
        golem.store.db.execute("UPDATE jobs SET created_at = '2000-01-01T00:00:00+00:00'")
        golem.scheduler.triage_reports()
        golem.harness.script = [RunResult("verdict", answer([]), session_id="s")]
        await golem.step()
        assert "- [code] Back off on GitHub errors — turned down: “the new backoff covers it”" in golem.harness.calls[-1].prompt

    run(go())


def test_a_triage_that_fails_or_asks_never_troubles_riggy(golem):
    async def go():
        reports(golem, 5)
        golem.store.put("triage", "on")
        job = golem.scheduler.triage_reports()
        golem.harness.script = [RunResult("verdict", answer([], verdict="blocked"), session_id="s")]
        await golem.step()  # a question back is nothing to wait on: its answer stands
        assert golem.store.job(job.id).state == "done"
        assert golem.chat.texts(STATUS)[-1].startswith("I triaged 5 report(s) about golem itself: nothing worth a fix this time.")

        reports(golem, 5, batch="b")
        golem.store.db.execute("UPDATE jobs SET created_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (job.id,))
        again = golem.scheduler.triage_reports()
        golem.harness.script = [RunResult("error", error="claude exited 1")] * 4
        for _ in range(4):
            await golem.step()
        assert golem.store.job(again.id).state == "failed"
        assert golem.chat.texts(STATUS)[-1] == "I couldn't finish triaging the reports about golem: claude exited 1"
        assert not [m for m in golem.store.db.execute("SELECT * FROM messages WHERE notify = 1")]  # nothing pushed
        assert all(r.source == "agent" or r.slug != INTERNAL for r in golem.store.reports())  # no incidents of its own

    run(go())


def test_a_triage_out_of_usage_waits_like_any_job(golem):
    async def go():
        reports(golem, 5)
        golem.store.put("triage", "on")
        job = golem.scheduler.triage_reports()
        golem.harness.script = [RunResult("limit", error="usage limit reached")]
        await golem.step()
        assert golem.store.job(job.id).state == "parked"
        soon = (datetime.now(UTC) + timedelta(minutes=31)).isoformat(timespec="seconds")
        assert golem.store.next_job(at=soon).id == job.id

    run(go())
