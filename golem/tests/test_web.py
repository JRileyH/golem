"""The status page: what anyone sees, signing in, and the changes Riggy can make there."""

from __future__ import annotations

import asyncio
import gzip
import json
from dataclasses import replace
from html import escape
from urllib.parse import unquote

import pytest
from starlette.testclient import TestClient

from golem import plan, web
from golem.control import Control
from golem.live import LiveRuns, view
from golem.web import COOKIE, Auth, create_app

SECRET = "ghp_supersecrettokenvalue"


@pytest.fixture
def page(golem, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", SECRET)
    golem.store.add_project("trees", "Trees <b>", "Flashcards")
    job = golem.store.add_job("trees", "new", "a flashcard game", "standard")
    client = TestClient(create_app(golem.settings, golem.store, golem.scheduler), base_url="https://golem.unlaunched.dev")
    client.job = job
    return client


def sign_in(client) -> None:
    r = client.post("/login", data={"password": "pw", "back": "/"}, follow_redirects=False)
    assert r.status_code == 303 and COOKIE in r.cookies


def act(client, **form):
    r = client.post("/act", data={"back": "/", **form}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]


def start(golem, job, at: str):
    """Give the job its steps and work them as far as the step labelled `at`."""
    steps = golem.scheduler.ensure_steps(job)
    for s in steps:
        if plan.label(s) == at:
            break
        golem.store.update_step(s.id, state="done", summary=f"{s.name} done", started_at="2026-10-23T10:00:00+00:00",
                                 ended_at="2026-10-23T10:05:00+00:00")  # fmt: skip
    return golem.store.steps(job.id)


def by_name(golem, job, name):
    return next(s for s in golem.store.steps(job.id) if s.name == name)


def test_anyone_sees_what_is_under_way(golem, page):
    assert page.get("/healthz").json() == {"ok": True, "harness": "claude"}
    html = page.get("/").text
    assert "Up next" in html and "Trees &lt;b&gt;" in html and "new project: a flashcard game" in html
    assert "Sign in" in html and "/act" not in html  # no controls signed out
    assert page.get("/?part=lanes").text.startswith("<section class='card signin-card'>")  # what the page refreshes with

    html = page.get("/p/trees").text
    assert "https://trees.unlaunched.dev/" in html and ">Queued</span>" in html and "Next up: plan it, starting <time" in html
    # Its 24 steps as the 7 pieces of work Riggy would name, in order; golem's shipping steps belong to them.
    names = ["Plan it", "Set up hosting", "Build the MVP", "You try it", "Build the rest", "Review", "Write the docs"]
    assert html.count("class='piece todo'") == 7 and [html.index(n) for n in names] == sorted(html.index(n) for n in names)
    assert "href='/jobs/1/planned/2'" in html  # each opens its sheet, by position until the job starts
    assert "Sign in (Settings) to see the conversation" in html and "/act" not in html


def test_the_running_step_shows_its_live_tail_without_secrets(golem, page):
    job = page.job
    steps = start(golem, job, "spec")
    spec = plan.current(steps)
    golem.store.update_step(spec.id, state="running", started_at="2026-10-23T10:00:00+00:00")
    golem.store.update_job(job.id, state="running")
    log = golem.settings.job_logs(job.id) / "001-spec.jsonl.gz"
    log.parent.mkdir(parents=True)
    events = [
        {"type": "assistant", "timestamp": "2026-10-23T10:01:00Z", "message": {"content": [{"type": "text", "text": "Reading the brief."}]}},
        {"type": "assistant", "timestamp": "2026-10-23T10:02:00Z", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": f"curl -H 'token {SECRET}' x", "description": "Check the API"}}]}},
        {"type": "user", "timestamp": "2026-10-23T10:03:00Z", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": f"echo {SECRET}"}]}},
    ]  # fmt: skip
    with gzip.open(log, "wt") as f:
        f.writelines(json.dumps(e) + "\n" for e in events)
    golem.store.start_run(job.id, "spec", "opus/high", str(log), spec.id)
    golem.store.db.execute("UPDATE runs SET started_at = '2026-10-23T10:00:00+00:00'")
    (golem.settings.job_logs(job.id) / "progress.txt").write_text("The brief makes sense.\n")

    html = page.get("/").text  # Home: what's being worked on, at a glance
    assert "Working on" in html and "Plan it: " in html and " so far" in html
    html = page.get("/p/trees").text
    assert "<ul class=notes><li>The brief makes sense.</li></ul>" in html
    assert "1:00</time><span>💬 Reading the brief." in html
    assert "→ Bash Check the API" in html  # what it's doing, but not the command or its output
    assert "curl" not in html and SECRET not in html and "echo" not in html

    sign_in(page)
    html = page.get("/p/trees").text
    assert "→ Bash curl -H &#x27;token •••&#x27; x" in html and "← echo •••" in html  # in full, secrets masked
    assert SECRET not in html


def test_signing_in(golem, page):
    r = page.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?msg=Wrong%20password." and COOKIE not in r.cookies
    sign_in(page)
    html = page.get("/settings").text
    assert "Sign out" in html and "Pause all" in html and "Run last" in page.get("/p/trees/settings").text
    cookie = page.cookies[COOKIE]
    page.post("/logout", follow_redirects=False)
    assert "signin-card" in page.get("/").text and "signin-card" in page.get("/settings").text
    assert "Sign%20in" in page.get("/p/trees/settings", follow_redirects=False).headers["location"]
    # A changed password signs everyone out.
    page.cookies.set(COOKIE, cookie, domain="golem.unlaunched.dev")
    assert "Sign out" in page.get("/settings").text
    assert not Auth(replace(golem.settings, web_password="new"), golem.store).valid(cookie)


def test_tokens_expire_and_guessing_is_slowed():
    class Store(dict):
        get = dict.get

        def put(self, k, v):
            self[k] = v

    now = [1000.0]

    class S:
        web_password = "pw"

    auth = Auth(S(), Store(), clock=lambda: now[0])
    token = auth.token()
    assert auth.valid(token) and not auth.valid(token + "0") and not auth.valid("123.abc") and not auth.valid(None)
    now[0] += 24 * 3600 + 1
    assert not auth.valid(token)
    for _ in range(web.MAX_FAILURES):
        assert auth.check_password("guess") is False
    assert auth.check_password("pw") is None  # even the right one, for a while
    now[0] += web.FAILURE_WINDOW + 1
    assert auth.check_password("pw") is True


def test_changes_need_signing_in_and_this_site(golem, page):
    start(golem, page.job, "spec")
    review = by_name(golem, page.job, "review")
    assert "Sign%20in%20first" in act(page, action="skip", job=page.job.id, step=review.id)
    sign_in(page)
    r = page.post("/act", data={"action": "skip", "job": page.job.id, "step": review.id}, headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert "Sign%20in%20first" in r.headers["location"]
    assert golem.store.step(review.id).state == "pending"
    assert act(page, action="skip", job=page.job.id, step=review.id, back="https://evil.example/").startswith("/?msg=")


def test_skipping_only_optional_steps_that_have_not_started(golem, page):
    sign_in(page)
    job = page.job
    start(golem, job, "buildout")
    review, buildout = by_name(golem, job, "review"), by_name(golem, job, "buildout")
    assert page.get(f"/jobs/{job.id}").text.count(">Skip</button>") == 2  # review and docs; not buildout or shipping
    assert ">Skip</button>" in page.get(f"/steps/{review.id}?part=sheet").text
    assert ">Skip</button>" not in page.get(f"/steps/{buildout.id}?part=sheet").text
    assert "Skipped%20review." in act(page, action="skip", job=job.id, step=review.id)
    assert golem.store.step(review.id).state == "skipped"
    assert golem.chat.texts(job.slug)[-1] == "Skipped review, from the status page."
    assert "can%27t%20be%20skipped" in act(page, action="skip", job=job.id, step=buildout.id)
    deploy = [s for s in golem.store.steps(job.id) if s.kind == "deploy"][-1]
    assert "can%27t%20be%20skipped" in act(page, action="skip", job=job.id, step=deploy.id)
    assert "Undo skip" in page.get(f"/steps/{review.id}?part=sheet").text
    assert "Put%20review%20back." in act(page, action="restore", job=job.id, step=review.id)
    assert golem.store.step(review.id).state == "pending"


def test_skipped_steps_are_passed_over(golem):
    async def go():
        from test_flow import new_project, verdict

        room = await new_project(golem, [verdict("done")] * 6)
        await golem.until("your check-in")
        job = golem.store.jobs()[0]
        control = Control(golem.store, golem.scheduler)
        await control.skip(job.id, by_name(golem, job, "review").id)
        assert golem.chat.texts(room)[-1] == "Skipped review, from the status page."
        await control.skip(job.id, by_name(golem, job, "checkin").id)
        await golem.finish()
        assert [c.label for c in golem.harness.calls[1:]] == ["spec", "scaffold", "mvp", "buildout", "docs"]
        assert golem.store.job(job.id).state == "done"
        assert [plan.label(s) for s in golem.store.steps(job.id)][-2:] == ["deploy", "done"]

    asyncio.run(go())


def test_skipping_the_checkin_carries_on(golem):
    async def go():
        from test_flow import new_project, verdict

        room = await new_project(golem, [verdict("done")] * 3)
        await golem.finish()
        job = golem.store.jobs()[0]
        assert job.state == "waiting"
        checkin = plan.current(golem.store.steps(job.id))
        await Control(golem.store, golem.scheduler).skip(job.id, checkin.id)
        assert golem.store.job(job.id).state == "queued"
        assert "as if you'd said it looks good" in golem.chat.texts(room)[-1]
        await golem.step()
        assert golem.harness.calls[-1].label == "buildout"

    asyncio.run(go())


def options_up(golem) -> str:
    """A change that asked for options, shown and waiting for Riggy's pick. Returns its room."""
    from test_flow import by_label, idle_project, three_styles

    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"options": [three_styles]})
        await golem.say(room, "!options a chibi hero")
        await golem.until("your pick")
        await golem.step()
        return room

    return asyncio.run(go())


def test_options_show_side_by_side_and_can_be_picked(golem):
    room = options_up(golem)
    job = golem.store.active_job("tree-cards")
    made, choose = by_name(golem, job, "options"), plan.current(golem.store.steps(job.id))
    client = TestClient(create_app(golem.settings, golem.store, golem.scheduler), base_url="https://golem.unlaunched.dev")
    html = client.get("/").text
    assert "Needs you" in html
    assert f"<img src='/jobs/{job.id}/options/{made.id}-b-1.png'" in html and "Pastel" in html  # on Home, to pick from
    assert "Pick B" not in html  # signed out
    assert "Waiting for your pick." in client.get("/p/tree-cards").text
    image = client.get(f"/jobs/{job.id}/options/{made.id}-b-1.png")
    assert image.content.startswith(b"\x89PNG")
    assert (image.headers["content-type"], image.headers["x-content-type-options"]) == ("image/png", "nosniff")
    for bad in (f"{made.id}-b-9.png", "golem.db", "..%2F..%2Fgolem.db"):
        assert client.get(f"/jobs/{job.id}/options/{bad}").status_code == 404
    sheet = client.get(f"/steps/{choose.id}?part=sheet").text
    assert "The options" in sheet and "Ink outline" in sheet and f"docs/options/job-{job.id}/c/" in sheet
    assert "Its options" in client.get(f"/steps/{made.id}?part=sheet").text

    sign_in(client)
    assert "Pick B" in client.get("/").text and "Pick C" in client.get(f"/steps/{choose.id}?part=sheet").text
    assert "Pick A" in client.get("/p/tree-cards").text and "Let it pick" in client.get("/").text
    assert "Picking the job back up" in unquote(act(client, action="note", job=job.id, value="B"))
    assert golem.store.job(job.id).state == "queued"
    said = golem.store.messages(room)[-1]
    assert (said.who, said.text) == ("riggy", "B")


def test_skipping_the_pick_leaves_it_to_the_next_step(golem):
    room = options_up(golem)
    job = golem.store.active_job("tree-cards")
    choose = plan.current(golem.store.steps(job.id))

    async def go():
        await Control(golem.store, golem.scheduler).skip(job.id, choose.id)
        assert golem.store.job(job.id).state == "queued"
        assert golem.chat.texts(room)[-1] == "Skipped your pick (the next step picks the strongest option), from the status page."
        await golem.step()
        call = golem.harness.calls[-1]
        assert call.label == "change" and "If he didn't pick one" in call.prompt

    asyncio.run(go())


def test_queue_order_settings_notes_pause_and_stop(golem, page):
    sign_in(page)
    golem.store.add_project("birds", "Birds", "s")
    birds = golem.store.add_job("birds", "new", "birds", "standard")
    trees = page.job
    html = page.get("/").text
    assert html.index("href='/p/trees'") < html.index("href='/p/birds'")  # up next, in the queue's order
    assert ">Run next</button>" not in page.get("/p/trees/settings").text and ">Run next</button>" in page.get("/p/birds/settings").text
    assert "Run Birds next" in page.get(f"/jobs/{birds.id}/planned/2?part=sheet").text
    act(page, action="move", job=birds.id, value="first")
    assert golem.store.next_job(at="9999").id == birds.id
    html = page.get("/").text
    assert html.index("href='/p/birds'") < html.index("href='/p/trees'")
    act(page, action="move", job=birds.id, value="last")
    assert golem.store.next_job(at="9999").id == trees.id

    act(page, action="setting", slug="trees", name="model", value="sonnet")
    assert golem.store.project("trees").model == "sonnet"
    assert "can%27t%20be%20gpt" in act(page, action="setting", slug="trees", name="model", value="gpt")
    act(page, action="setting", slug="trees", name="model", value="")
    assert golem.store.project("trees").model == ""  # back to each step's default

    golem.store.update_job(trees.id, state="waiting")
    assert "Picking%20the%20job%20back%20up" in act(page, action="note", job=trees.id, value="use oak first")
    assert golem.store.job(trees.id).state == "queued" and golem.store.pending_notes(trees.id)[-1][1] == "use oak first"

    act(page, action="pause", slug="trees")
    assert golem.store.project("trees").paused and "Resume project" in page.get("/p/trees/settings").text
    assert "Paused: nothing starts" in page.get("/p/trees").text
    act(page, action="resume", slug="trees")
    act(page, action="pause-all")
    assert golem.scheduler.paused and "Work is paused" in page.get("/").text
    act(page, action="resume-all")
    act(page, action="kill", job=birds.id)
    assert golem.store.job(birds.id).state == "killed"
    assert "That%20job%20has%20finished." in act(page, action="kill", job=birds.id)


def test_job_page(golem, page):
    start(golem, page.job, "mvp")
    html = page.get(f"/jobs/{page.job.id}").text
    assert "Agent runs" in html and "8 steps done" in html and " open>" in html
    assert page.get("/jobs/99").status_code == 404
    assert page.get("/steps/999").status_code == 404 and page.get("/jobs/1/planned/99").status_code == 404


def test_a_step_before_it_starts_can_get_its_own_model_effort_and_context(golem, page):
    sign_in(page)
    job = page.job
    assert golem.store.steps(job.id) == []  # not started: its steps are planned, not stored
    html = page.get(f"/jobs/{job.id}/planned/2?part=sheet").text  # spec
    assert "Runs on</dt><dd>opus/high" in html and "default: opus" in html
    act(page, action="step-setting", job=job.id, position=2, name="model", value="haiku")
    spec = by_name(golem, job, "spec")  # the job's steps are stored now
    assert spec.model == "haiku" and len(golem.store.steps(job.id)) == 24
    act(page, action="step-setting", job=job.id, step=spec.id, name="effort", value="low")
    html = page.get(f"/steps/{spec.id}?part=sheet").text
    assert "haiku/low" in html and "model from this step" in html
    assert "spec%20gets%20that" in act(page, action="note", job=job.id, step=spec.id, value="keep it to one page")
    assert "keep it to one page" in page.get(f"/steps/{spec.id}?part=sheet").text
    assert golem.store.pending_notes(job.id) == []  # not for whichever step is next
    act(page, action="step-setting", job=job.id, step=spec.id, name="model", value="")
    assert golem.store.step(spec.id).model is None

    golem.store.update_step(spec.id, started_at="2026-10-23T10:00:00+00:00")
    assert "haven%27t%20started" in act(page, action="step-setting", job=job.id, step=spec.id, name="model", value="opus")
    assert "has%20started" in act(page, action="note", job=job.id, step=spec.id, value="too late")


def test_step_context_and_settings_reach_only_that_step(golem):
    async def go():
        from test_flow import new_project, verdict

        await new_project(golem, [verdict("done")] * 4)
        job = golem.store.jobs()[0]
        control = Control(golem.store, golem.scheduler)
        golem.scheduler.ensure_steps(job)
        scaffold = by_name(golem, job, "scaffold")
        await control.note(job.id, "use plain CSS", scaffold.id)
        await control.step_setting(job.id, scaffold.id, 0, "model", "sonnet")
        golem.store.set_default("spec", "effort", "low")
        await golem.until("PR")
        spec_call, scaffold_call = golem.harness.calls[-2:]
        assert (spec_call.model, spec_call.effort) == ("opus", "low") and "use plain CSS" not in spec_call.prompt
        assert (scaffold_call.model, scaffold_call.effort) == ("sonnet", "high") and "use plain CSS" in scaffold_call.prompt
        assert [r["model"] for r in golem.store.runs(job.id)] == ["opus/low", "sonnet/high"]

    asyncio.run(go())


def test_insights_and_defaults(golem, page):
    job = page.job
    start(golem, job, "mvp")
    spec = by_name(golem, job, "spec")
    run = golem.store.start_run(job.id, "spec", "opus/high", "/nowhere", spec.id)
    golem.store.finish_run(run, "verdict", cost_usd=1.5, input_tokens=900_000, output_tokens=10_000)
    golem.store.db.execute("UPDATE runs SET started_at = '2020-01-01T10:00:00+00:00', ended_at = '2020-01-01T10:20:00+00:00'")
    golem.store.db.execute(
        "UPDATE steps SET started_at = '2020-01-01T10:00:00+00:00', ended_at = '2020-01-01T10:05:00+00:00' WHERE started_at IS NOT NULL"
    )
    html = page.get("/insights?days=all").text
    assert "Where the time goes" in html and "$1.50" in html and "910k" in html
    assert "The spec steps use 100% of the Claude usage" in html
    assert "Nothing has run" in page.get("/insights?days=7").text  # those runs were a while ago
    golem.store.db.execute("UPDATE runs SET started_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-1 day'), "
                            "ended_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-1 day', '+20 minutes')")
    golem.store.db.execute("UPDATE steps SET started_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-1 day'), "
                            "ended_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-1 day', '+5 minutes') WHERE started_at IS NOT NULL")
    html = page.get("/settings").text  # the defaults, with how each kind of step has done this month
    assert "spec</b> <span class=muted>20m a step · $1.50 a step" in html
    assert "Sign in to change them" in html and "/act" not in html.split("id=defaults")[1]

    sign_in(page)
    assert "Every%20docs%20step" in act(page, action="default", kind="docs", name="model", value="haiku")
    assert golem.store.defaults()["docs"] == ("haiku", "")
    assert "No%20kind%20of%20step" in act(page, action="default", kind="nope", name="model", value="haiku")
    act(page, action="default", kind="*", name="effort", value="medium")
    docs = by_name(golem, job, "docs")
    assert "haiku/medium" in page.get(f"/steps/{docs.id}?part=sheet").text
    html = page.get("/settings").text
    assert "docs</b> <span class=muted>not run yet · suggested sonnet/medium" in html and "reset-defaults" in html

    assert "back%20to%20golem" in act(page, action="reset-defaults")
    assert golem.store.defaults()["docs"] == ("sonnet", "medium") and "*" not in golem.store.defaults()
    assert "suggested sonnet" not in page.get("/settings").text  # each at its suggestion
    assert "sonnet/medium" in page.get(f"/steps/{docs.id}?part=sheet").text


def test_proposals_how_often_and_thumbs_up_or_down(golem, page):
    golem.store.update_job(page.job.id, state="done")
    looked = golem.store.add_job("trees", "change", "Look at its features for things to propose", "proposals")
    golem.store.update_job(looked.id, state="done")
    offline, bigger = golem.store.add_proposals("trees", looked.id, "features", [("Offline <mode>", "Cache it.\n\nWhy: hikes."), ("Bigger cards", "Fill the screen.")])
    assert "golem proposes" not in page.get("/p/trees").text  # signed out: not shown
    sign_in(page)
    html = page.get("/p/trees").text
    assert "golem proposes" in html and "Offline &lt;mode&gt;" in html and "Cache it.<br><br>Why: hikes." in html
    assert html.index("Offline &lt;mode&gt;") < html.index("Bigger cards")  # oldest first
    assert html.count("name=value value=accept") == 2 and "name=proposal value='1'" in html

    assert "Thumbs%20up%20or%20down" in act(page, action="proposal", proposal=offline.id, value="maybe")
    assert "golem%20takes%20that%20into%20account" in act(page, action="proposal", proposal=offline.id, value="reject", reason="always online")
    assert golem.store.proposal(offline.id).state == "rejected"
    assert "decided%20already" in act(page, action="proposal", proposal=offline.id, value="accept")
    location = act(page, action="proposal", proposal=bigger.id, value="accept")
    built = golem.store.active_job("trees")
    assert f"job%20{built.id}%20builds%20it" in location and built.request.startswith("Bigger cards\n\nFill the screen.")
    assert "golem proposes" not in page.get("/p/trees").text  # nothing left to decide

    html = page.get("/p/trees/settings").text
    assert "<h2>Proposals</h2>" in html and "<option value='' selected>off</option>" in html
    assert "👎 Offline &lt;mode&gt;<span class=sub>“always online”</span>" in html and f"👍 Bigger cards <a href='/jobs/{built.id}'>" in html
    act(page, action="setting", slug="trees", name="proposals", value="hourly")
    assert golem.store.project("trees").proposals == "hourly" and "<option selected>hourly</option>" in page.get("/p/trees/settings").text
    assert "Proposals hourly, when there's nothing else to do, from the status page." in [m.text for m in golem.store.messages("trees")]
    assert "can%27t%20be%20yearly" in act(page, action="setting", slug="trees", name="proposals", value="yearly")
    assert "<option selected>5</option>" in page.get("/p/trees/settings").text  # waiting on him at most: 5 unless he says
    act(page, action="setting", slug="trees", name="proposals_max", value="10")
    assert golem.store.project("trees").proposals_max == 10 and "<option selected>10</option>" in page.get("/p/trees/settings").text
    assert "Pick%20how%20many" in act(page, action="setting", slug="trees", name="proposals_max", value="7")
    assert "Pick%20how%20many" in act(page, action="setting", slug="trees", name="proposals_max", value="")
    act(page, action="setting", slug="trees", name="proposals", value="")
    assert golem.store.project("trees").proposals == "off"


def test_kinds_of_proposal_are_all_on_until_riggy_unticks_one(golem, page):
    from golem.proposals import LENSES

    sign_in(page)
    html = page.get("/p/trees/settings").text
    assert html.count("name=value value=on checked") == len(LENSES)  # every kind, each ticked
    for x in LENSES:  # what each kind asks the worker to look at, word for word
        assert f"<b>{x.name.capitalize()}</b></label><span class=sub>{escape(x.focus)}</span>" in html

    assert "%3A%20no%20proposals%20about%20its%20features%20now." in act(page, action="proposal-kind", slug="trees", name="features")
    assert golem.store.project("trees").proposals_off == "features"
    act(page, action="proposal-kind", slug="trees", name="security")
    assert golem.store.project("trees").proposals_off == "features,security"  # in LENSES order
    html = page.get("/p/trees/settings").text
    assert html.count("name=value value=on checked") == len(LENSES) - 2
    assert "No proposals about its security now, from the status page." in [m.text for m in golem.store.messages("trees")]
    act(page, action="proposal-kind", slug="trees", name="features", value="on")
    assert golem.store.project("trees").proposals_off == "security"

    assert "no%20kind%20of%20proposal%20called%20gameplay" in act(page, action="proposal-kind", slug="trees", name="gameplay")
    for x in LENSES[:-1]:
        act(page, action="proposal-kind", slug="trees", name=x.name)
    assert "Leave%20one%20kind%20on" in act(page, action="proposal-kind", slug="trees", name=LENSES[-1].name)
    assert golem.store.project("trees").proposals_off == ",".join(x.name for x in LENSES[:-1])


def test_tags_in_settings_and_on_a_project(golem, page):
    html = page.get("/settings").text
    assert "id=tags" in html and "<b>Game</b><span>0 projects</span>" in html and "tag-add" not in html  # signed out: a list
    sign_in(page)
    assert "Added%20the%20Dev%20Tool%20tag" in act(page, action="tag-add", value="  Dev   Tool ")
    assert [(t.name, t.color) for t in golem.store.tags()] == [("Game", 7), ("Dev Tool", 1)]  # a color no tag has
    assert "already" in act(page, action="tag-add", value="game")
    assert "A%20tag%20is" in act(page, action="tag-add", value="<b>")

    act(page, action="setting", slug="trees", name="tag", value="dev tool")
    assert golem.store.project("trees").tag == "Dev Tool"
    assert "Tagged Dev Tool, from the status page." in [m.text for m in golem.store.messages("trees")]
    assert "<option selected>Dev Tool</option>" in page.get("/p/trees/settings").text
    assert "<b>Dev Tool</b><span>1 project</span>" in page.get("/settings").text

    assert "on%20its%20projects" in act(page, action="tag-rename", name="Dev Tool", value="Developer Tool")
    assert golem.store.project("trees").tag == "Developer Tool"
    assert "already" in act(page, action="tag-rename", name="Developer Tool", value="GAME")
    act(page, action="tag-color", name="Developer Tool", value="5")
    assert golem.store.tag("developer tool").color == 5
    assert "Pick%20one" in act(page, action="tag-color", name="Developer Tool", value="9")
    assert "1%20project%20has%20none" in act(page, action="tag-delete", name="Developer Tool")
    assert golem.store.project("trees").tag == "" and [t.name for t in golem.store.tags()] == ["Game"]
    assert "No%20tag%20called" in act(page, action="setting", slug="trees", name="tag", value="Nope")
    act(page, action="setting", slug="trees", name="tag", value="")
    assert "No tag now, from the status page." in [m.text for m in golem.store.messages("trees")]


def test_public_view_keeps_what_it_is_doing_but_not_how():
    live = LiveRuns(env={"DEPLOYER_SECRET": "abcdefgh123", "HOME": "/home/golem"})
    lines = live.describe(
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "1", "name": "Read", "input": {"file_path": "src/a.ts"}},
                                                      {"type": "tool_use", "id": "2", "name": "Bash", "input": {"command": "echo abcdefgh123"}}]}},
        None,
    )  # fmt: skip
    assert [line.text for line in view(lines, signed_in=False)] == ["Read src/a.ts", "Bash"]
    assert [line.text for line in view(lines, signed_in=True)] == ["Read src/a.ts", "Bash echo •••"]


# --- the projects page -------------------------------------------------------------


def test_projects_get_a_card_when_they_go_live_and_again_when_a_job_ends(golem):
    async def go():
        from test_flow import new_project, verdict

        await new_project(golem, [verdict("done", "a flashcard deck")] * 3)
        await golem.until("mvp")  # the scaffold deployed: live for the first time
        card = golem.store.card("tree-cards")
        assert (card.tagline, card.kind, card.image) == ("Name that tree", "game", "tree-cards.jpg")
        assert golem.shots == ["https://tree-cards.unlaunched.dev/"] and len(golem.card_calls) == 1
        call = golem.card_calls[0]
        assert (call.model, call.label) == ("haiku", "card") and "a flashcard game for identifying" in call.prompt
        await golem.until("your check-in")  # the MVP deployed too, but the card stays until the job ends
        assert len(golem.card_calls) == 1
        await golem.step()  # asks Riggy to try it
        await golem.say("tree-cards", "looks good")
        await golem.finish()
        assert len(golem.card_calls) == 2 and len(golem.shots) == 2

        # Deleting the project takes its card and screenshot with it.
        image = golem.settings.state_dir / "cards" / "tree-cards.jpg"
        assert image.exists()
        await golem.say("tree-cards", "!delete tree-cards")
        assert golem.store.card("tree-cards") is None and not image.exists()

    asyncio.run(go())


def test_a_card_that_cant_be_made_keeps_what_it_had(golem):
    from golem.cards import Cards
    from golem.harness import FakeHarness, RunResult

    async def go():
        project = golem.store.add_project("trees", "Trees", "Learn trees.")
        broken = Cards(golem.settings, golem.store, FakeHarness(lambda call: RunResult("error", error="boom")),
                       shoot=lambda url, path: _false())  # fmt: skip
        card = await broken.refresh(project, golem.settings.workspace_dir / "trees")
        assert (card.description, card.kind, card.image) == ("Learn trees.", "app", None)  # the summary stands in
        await golem.scheduler.cards.refresh(project, golem.settings.workspace_dir / "trees")
        card = await broken.refresh(project, golem.settings.workspace_dir / "trees")
        assert (card.tagline, card.image) == ("Name that tree", "trees.jpg")  # the last good one stays

    async def _false():
        return False

    asyncio.run(go())


def test_the_projects_page(golem, page):
    store = golem.store
    store.add_project("birds", "Birds", "Spot birds.")  # not live: not shown
    store.add_job("birds", "new", "birds", "standard")
    start(golem, page.job, "mvp")  # trees deployed its scaffold
    projects = TestClient(create_app(golem.settings, store, golem.scheduler), base_url="https://projects.unlaunched.dev")
    html = projects.get("/").text
    assert "Trees &lt;b&gt;" in html and "Flashcards" in html and ">Open <span" in html  # no card yet: the summary
    assert "Birds" not in html and "https://trees.unlaunched.dev/" in html and "class=placeholder" in html
    assert projects.get("/jobs/1").status_code == 404 and projects.get("/insights").status_code == 404  # only this page

    asyncio.run(golem.scheduler.refresh_card(store.project("trees")))
    html = projects.get("/").text
    assert ">Play <span" in html and "Name that tree" in html
    assert "class='tag" not in html and "class=filters" not in html  # no tag, and nothing to filter
    # A project's tag shows in the tag's color, the same on every card; with more than one sort, filters.
    store.update_project("trees", tag="Game")
    store.update_job(store.add_job("birds", "change", "more birds", "standard").id, state="done")  # live, untagged
    html = projects.get("/").text
    assert "<p class='tag c7'>Game</p>" in html and "data-tag='Game'" in html and "data-tag='-'" in html
    assert "<button data-tag='Game'>Game</button><button data-tag='-'>Other</button>" in html
    store.update_tag("Game", color=3)
    assert "<p class='tag c3'>Game</p>" in projects.get("/").text
    image = html.split("<img src='")[1].split("'")[0]
    assert image.startswith("/cards/trees.jpg?v=")
    r = projects.get(image)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert projects.get("/cards/birds.jpg").status_code == 404 and projects.get("/cards/..%2Fgolem.db").status_code == 404
    assert "Name that tree" in page.get("/projects").text  # on golem's own host too, linked from the status page


# --- the web app: conversations ---------------------------------------------------


@pytest.fixture
def app(golem):
    """The web app with the bot behind it, as golem runs it, signed out."""
    return TestClient(create_app(golem.settings, golem.store, golem.scheduler, golem.bot), base_url="https://golem.unlaunched.dev")


def say(client, channel, text, **more):
    r = client.post("/say", data={"channel": channel, "text": text, **more}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]


def test_the_inbox_takes_new_ideas(golem, app):
    from test_flow import TREES

    assert "Sign in (Settings) to see the inbox" in app.get("/inbox").text
    assert say(app, "inbox", "a tree game") == "/inbox?msg=Sign%20in%20first.#end"
    r = app.post("/login", data={"password": "pw", "keep": "1", "back": "/inbox"}, follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert "max-age=7776000" in cookie and "samesite=lax" in cookie
    golem.harness.script = [TREES]
    assert say(app, "inbox", "a flashcard game for identifying northeastern us trees") == "/p/tree-cards#end"  # on to its page
    assert golem.store.project("tree-cards") is not None
    assert "Describe a new idea" in app.get("/").text  # Home takes ideas too
    html = app.get("/inbox").text
    assert "a flashcard game for identifying northeastern us trees" in html and "New project: Tree Cards" in html
    assert "Describe a new idea" in html  # the box to write more


def test_a_project_page_is_its_conversation(golem, app):
    from test_flow import TREES, by_label

    golem.harness.script = [TREES]
    sign_in(app)
    say(app, "inbox", "a flashcard game for identifying northeastern us trees")
    job = golem.store.active_job("tree-cards")
    html = app.get("/p/tree-cards").text
    assert "Tree Cards" in html and "class=pieces" in html and "You said: “a flashcard game" in html
    assert "href='/p/tree-cards/settings'" in html
    assert "A note for the job&#x27;s next step" in html and "Options first" not in html  # a job's under way
    html = app.get("/p").text  # the list of conversations: golem's own, then the projects', each opening at its latest
    assert html.index("href='/inbox#end'") < html.index("href='/p/tree-cards#end'") and "You said: “a flashcard game" in html

    golem.store.update_job(job.id, state="done")
    assert "Options first" in app.get("/p/tree-cards").text
    golem.harness.script = by_label({"options": []})
    assert say(app, "tree-cards", "a new logo", command="options") == "/p/tree-cards#end"
    options = golem.store.active_job("tree-cards")
    assert (options.playbook, options.request) == ("options", "a new logo")
    golem.store.update_job(options.id, state="waiting")
    assert "Your answer" in app.get("/p/tree-cards").text
    say(app, "tree-cards", "B")
    assert golem.store.job(options.id).state == "queued"  # an answer picks the job back up

    say(app, "tree-cards", "Leaf Cards", command="rename")
    assert golem.store.project("tree-cards").name == "Leaf Cards"
    say(app, "tree-cards", "standard", command="mode")
    assert "mode standard" in golem.store.messages("tree-cards")[-1].text
    assert "type%20tree-cards%20exactly" in say(app, "tree-cards", "trees", command="delete")
    assert "golem can" in unquote(say(app, "status", "hello"))
    assert "Write something first" in unquote(say(app, "tree-cards", "x", command="kill"))
    golem.store.update_job(golem.store.active_job("tree-cards").id, state="done")
    assert say(app, "tree-cards", "tree-cards", command="delete") == "/inbox#end"
    assert golem.store.project("tree-cards") is None and app.get("/p/tree-cards").status_code == 404


def test_a_long_conversation_shows_its_latest_messages_first(golem, app):
    from golem.web import SHOWN

    golem.store.add_project("trees", "Trees", "")
    for n in range(SHOWN + 5):
        golem.store.add_message("trees", "golem", f"note {n}.")
    sign_in(app)
    html = app.get("/p/trees").text
    assert f"note {SHOWN + 4}." in html and "note 5." in html and "note 4." not in html
    assert f"href='?show={2 * SHOWN}' data-earlier>Show earlier messages" in html
    assert "class=to-latest hidden" in html  # shown by the page's script once scrolled up
    html = app.get(f"/p/trees?part=lanes&show={2 * SHOWN}").text  # what the link loads in place
    assert "note 0." in html and "Show earlier" not in html
    for n in range(SHOWN + 1):
        golem.store.add_message("status", "golem", f"news {n}.")
    html = app.get("/inbox").text  # golem's own conversation: new ideas and news, together
    assert f"news {SHOWN}." in html and "news 0." not in html and "Show earlier messages" in html


def test_conversations_are_private_and_writing_needs_this_site(golem, app):
    from test_flow import TREES

    golem.harness.script = [TREES]
    sign_in(app)
    say(app, "inbox", "a flashcard game for identifying northeastern us trees")
    r = app.post("/say", data={"channel": "tree-cards", "text": "delete everything"}, headers={"origin": "https://evil.example"},
                 follow_redirects=False)  # fmt: skip
    assert "Sign%20in%20first" in r.headers["location"] and golem.store.messages("tree-cards")[-1].text != "delete everything"
    app.post("/logout", follow_redirects=False)
    html = app.get("/p/tree-cards").text
    assert "Sign in (Settings) to see the conversation" in html and "You said:" not in html and "class=composer" not in html
    assert "You said:" not in app.get("/p").text and "Tree Cards" in app.get("/p").text


# --- the installable app and notifications ----------------------------------------


def test_installable_with_its_own_icon_and_service_worker(app):
    manifest = app.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["display"] == "standalone" and manifest.json()["start_url"] == "/"
    icon = app.get("/icon-180.png")
    assert icon.content.startswith(b"\x89PNG") and icon.content[16:24] == (180).to_bytes(4, "big") * 2
    assert app.get("/icon-64.png").status_code == 404
    sw = app.get("/sw.js")
    assert sw.headers["content-type"].startswith("text/javascript") and "showNotification" in sw.text
    assert "apple-touch-icon" in app.get("/").text and "serviceWorker.register" not in app.get("/").text  # signed out
    sign_in(app)
    assert "serviceWorker.register" in app.get("/").text and "Turn on for this device" in app.get("/settings").text


def test_devices_turn_notifications_on_and_get_what_wants_riggy(golem, app):
    from golem.push import Push
    from pywebpush import WebPushException

    sent = []

    def send(**kwargs):
        if kwargs["subscription_info"]["endpoint"].endswith("/gone"):
            raise WebPushException("gone", response=type("R", (), {"status_code": 410})())
        sent.append((kwargs["subscription_info"]["endpoint"], json.loads(kwargs["data"]), kwargs["vapid_claims"]))

    push = Push(golem.settings, golem.store, send)
    golem.talk.notify = push.notify
    app = TestClient(create_app(golem.settings, golem.store, golem.scheduler, golem.bot, push), base_url="https://golem.unlaunched.dev")
    sub = {"endpoint": "https://push.example/phone", "keys": {"p256dh": "BPk", "auth": "a1"}, "label": "iPhone"}
    assert app.post("/push/subscribe", json=sub).status_code == 403  # signed out
    sign_in(app)
    assert app.post("/push/subscribe", json=sub, headers={"origin": "https://evil.example"}).status_code == 403
    assert app.post("/push/subscribe", json={"endpoint": "http://x", "keys": {}}).status_code == 400
    assert app.post("/push/subscribe", json=sub).json() == {"ok": True}
    golem.store.add_subscription("https://push.example/gone", {"p256dh": "x", "auth": "y"})
    assert len(app.get("/push/key").json()["key"]) == 87  # a P-256 public key, base64url
    assert push.public_key() == Push(golem.settings, golem.store).public_key()  # made once, kept

    asyncio.run(golem.talk.send("inbox", "Noted.", notice=True))
    assert sent == []  # news, not for his phone
    asyncio.run(golem.talk.send("inbox", "Question: photos or drawings?"))
    [(endpoint, payload, claims)] = sent
    assert endpoint == "https://push.example/phone" and claims["sub"] == "https://golem.unlaunched.dev"
    assert payload == {"title": "golem", "body": "Question: photos or drawings?", "url": "/inbox#end", "tag": "inbox"}
    assert [s["endpoint"] for s in golem.store.subscriptions()] == ["https://push.example/phone"]  # the gone one's forgotten
    assert app.post("/push/test", json={}).json() == {"sent": 1}
    app.post("/push/unsubscribe", json={"endpoint": "https://push.example/phone"})
    assert golem.store.subscriptions() == []
