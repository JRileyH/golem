"""The status page: what anyone sees, signing in, and the changes Riggy can make there."""

from __future__ import annotations

import asyncio
import gzip
import json
import zlib
from dataclasses import replace
from html import escape
from urllib.parse import quote, unquote

import pytest
from starlette.testclient import TestClient

from golem import plan, pwa, web
from golem.control import Control
from golem.live import LiveRuns, view
from golem.scheduler import Outcome
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
    """Start the job and walk it, each step done as it comes, as far as the step labelled `at`."""
    golem.scheduler.ensure_steps(job)
    for _ in range(200):
        s = plan.current(golem.store.steps(job.id))
        if s is None or plan.label(s) == at:
            break
        closed = {"state": "done", "summary": f"{s.name} done", "started_at": "2026-10-23T10:00:00+00:00", "ended_at": "2026-10-23T10:05:00+00:00"}
        golem.scheduler.walk_on(golem.store.job(job.id), s, Outcome("done", f"{s.name} done"), closed)
    return golem.store.steps(job.id)


def by_name(golem, job, name):
    """A step of the job by name: written already, or still to come (plan.planned, id 0)."""
    return next(s for s in plan.planned(golem.settings, golem.store, golem.store.job(job.id)) if s.name == name)


def at(s) -> dict:
    """The form fields that say which step: its id, or its position if it's still to come."""
    return {"step": s.id} if s.id else {"position": s.position}


def sheet_of(job, s) -> str:
    return f"/steps/{s.id}?part=sheet" if s.id else f"/jobs/{job.id}/planned/{s.position}?part=sheet"


def test_anyone_sees_what_is_under_way(golem, page):
    assert page.get("/healthz").json() == {"ok": True, "harness": "claude"}
    html = page.get("/").text  # Now: every job, by where it is, drawn by its route
    assert "Up next" in html and "Trees &lt;b&gt;" in html and "a flashcard game" in html and "Web app" in html
    assert "class='route small'" in html and "class='rc todo'" in html and " more</a>" in html  # the next few blocks, then how many more
    assert "Sign in" in html and "/act" not in html  # no controls signed out
    assert page.get("/?part=lanes").text.startswith("<section class='card signin-card'>")  # what the page refreshes with

    html = page.get("/p/trees").text  # its jobs, and the one it opens on
    assert "https://trees.unlaunched.dev/" in html and ">Queued</span>" in html and "Next up: plan it, starting <time" in html
    assert "data-focus=list" in html and "<h2>Trees &lt;b&gt;</h2>" not in html  # a phone shows the list; the name's escaped
    # Its steps as the blocks of its workflow, in order: each container (Ship) one row.
    names = ["set up", "spec", "scaffold", "Ship", "mvp", "your check-in", "design", "guide", "apply", "buildout", "review", "docs"]
    rows = html[html.index("class=route-list") :]
    assert rows.count("<li class='rl todo") == 16 and [rows.index(f"<b>{n}</b>") for n in names] == sorted(rows.index(f"<b>{n}</b>") for n in names)
    assert "href='/jobs/1/planned/2'" in html  # each opens its sheet, by position until the job starts
    assert "Sign in (Settings) to see what golem and you said about this job." in html and "/act" not in html
    assert "class='card ask-form'" not in html  # signed out: no asking


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
        {"type": "assistant", "timestamp": "2026-10-23T10:04:00Z", "message": {"content": [
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "npx playwright test", "description": "Run the e2e tests"}}]}},
    ]  # fmt: skip
    with gzip.open(log, "wt") as f:
        f.writelines(json.dumps(e) + "\n" for e in events)
    golem.store.start_run(job.id, "spec", "opus/high", str(log), spec.id)
    golem.store.db.execute("UPDATE runs SET started_at = '2026-10-23T10:00:00+00:00'")
    (golem.settings.job_logs(job.id) / "progress.txt").write_text("The brief makes sense.\n")

    html = page.get("/").text  # Now: what's running, and where
    assert "Running" in html and "Plan it: " in html and " so far" in html and "class='rc now'" in html
    html = page.get("/p/trees").text
    assert "<ul class=notes><li>The brief makes sense.</li></ul>" in html
    assert "1:00</time><span>💬 Reading the brief." in html
    assert "→ Bash Check the API" in html  # what it's doing, but not the command or its output
    assert "curl" not in html and SECRET not in html and "echo" not in html
    assert "<p class=glance>now: Bash Run the e2e tests for " in html and "npx" not in html  # at a glance, on its row (glance.py)
    assert "<p class=glance>tests " in html

    sign_in(page)
    html = page.get("/p/trees").text
    assert "→ Bash curl -H &#x27;token •••&#x27; x" in html and "← echo •••" in html  # in full, secrets masked
    assert SECRET not in html
    assert "<p class=glance>now: npx playwright test for " in html
    assert "<p class=glance>now: npx playwright test for " in page.get(sheet_of(job, spec)).text  # and on its sheet


def test_a_job_goes_by_its_title_and_its_page_keeps_the_request(golem, page):
    golem.store.update_job(page.job.id, state="done")
    words = "I'd like you to redo the library page\nso it's a grid of covers"
    job = golem.store.add_job("trees", "change", words, "feature", title="Library View Redesign")

    home = page.get("/").text
    assert "Library View Redesign" in home and "I&#x27;d like you to" not in home
    html = page.get(f"/jobs/{job.id}").text  # its project's page, open on it
    assert "<h2>Library View Redesign</h2>" in html and "data-focus=job" in html
    assert f"<h2>The request</h2><p class=asked>{escape(words)}</p>" in html  # in full, where it came from

    untitled = golem.store.add_job("trees", "change", "make the cards flip faster please\nand bigger", "feature")
    assert plan.title(untitled) == "make the cards flip faster please"  # no model: the request's first line


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
    assert "Sign%20in%20first" in act(page, action="skip", job=page.job.id, **at(review))
    sign_in(page)
    r = page.post("/act", data={"action": "skip", "job": page.job.id, **at(review)}, headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert "Sign%20in%20first" in r.headers["location"]
    assert by_name(golem, page.job, "review").state == "pending"
    assert act(page, action="skip", job=page.job.id, **at(review), back="https://evil.example/").startswith("/?msg=")


def test_skipping_only_optional_steps_that_have_not_started(golem, page):
    sign_in(page)
    job = page.job
    start(golem, job, "buildout")
    review, buildout = by_name(golem, job, "review"), by_name(golem, job, "buildout")
    assert page.get(f"/jobs/{job.id}").text.count(">Skip</button>") == 2  # review and docs; not buildout or shipping
    assert ">Skip</button>" in page.get(sheet_of(job, review)).text
    assert ">Skip</button>" not in page.get(sheet_of(job, buildout)).text
    assert "Skipped%20review." in act(page, action="skip", job=job.id, **at(review))
    assert by_name(golem, job, "review").state == "skipped"
    assert golem.chat.texts(job.slug)[-1] == "Skipped review, from the status page."
    assert "can%27t%20be%20skipped" in act(page, action="skip", job=job.id, **at(buildout))
    deploy = [s for s in plan.planned(golem.settings, golem.store, golem.store.job(job.id)) if s.kind == "deploy"][-1]
    assert "can%27t%20be%20skipped" in act(page, action="skip", job=job.id, **at(deploy))
    assert "Undo skip" in page.get(sheet_of(job, review)).text
    assert "Put%20review%20back." in act(page, action="restore", job=job.id, **at(review))
    assert by_name(golem, job, "review").state == "pending"


def test_skipped_steps_are_passed_over(golem):
    async def go():
        from test_flow import new_project, verdict

        room = await new_project(golem, [verdict("done")] * 6)
        await golem.until("your check-in")
        job = golem.store.jobs()[0]
        control = Control(golem.store, golem.scheduler)
        review = by_name(golem, job, "review")
        await control.skip(job.id, review.id, review.position)
        assert golem.chat.texts(room)[-1] == "Skipped review, from the status page."
        checkin = by_name(golem, job, "checkin")
        await control.skip(job.id, checkin.id, checkin.position)
        design = by_name(golem, job, "design")
        await control.skip(job.id, design.id, design.position)  # and with it the guide and restyle
        golem.workspace.has_work = False  # as in git: nothing new on the branch after the skipped pass
        await golem.until("buildout")
        deploys = [s for s in golem.store.steps(job.id) if s.kind == "deploy"]
        assert [s.state for s in deploys[:3]] == ["done", "done", "skipped"] and deploys[2].summary.endswith("is live already.")
        assert len(golem.deployer.deploys) == 2  # not the same commit again
        golem.workspace.has_work = True
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
        assert golem.harness.calls[-1].label == "design"  # the design pass comes next, then the buildout

    asyncio.run(go())


def options_up(golem) -> str:
    """A change that asked for options, shown and waiting for Riggy's pick. Returns its room."""
    from test_flow import by_label, idle_project, three_styles

    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"options": [three_styles]})
        await golem.ask(room, "a chibi hero", workflow="options")
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


def test_a_design_doc_is_served_in_a_sandbox(golem, page):
    from golem import options

    folder = options.folder(golem.settings, page.job.id)
    folder.mkdir(parents=True)
    (folder / "12-doc.html").write_text("<!doctype html><title>Directions</title><section class=page>A · Field guide</section>")
    (folder / "12-p-1.png").write_bytes(b"\x89PNG rest")
    doc = page.get(f"/jobs/{page.job.id}/options/12-doc.html")
    assert doc.status_code == 200 and "A · Field guide" in doc.text
    assert doc.headers["content-security-policy"] == web.DOC_POLICY and web.DOC_POLICY.startswith("sandbox; default-src 'none';")
    assert doc.headers["content-type"] == "text/html; charset=utf-8"
    assert (doc.headers["x-content-type-options"], doc.headers["referrer-policy"], doc.headers["cache-control"]) == (
        "nosniff", "no-referrer", "no-cache"
    )
    assert page.get(f"/jobs/{page.job.id}/options/12-p-1.png").headers["content-type"] == "image/png"  # a shared page
    for bad in ("13-doc.html", "12-doc.htm", "doc.html", "..%2F12-doc.html", "12-doc.html%00.png"):
        assert page.get(f"/jobs/{page.job.id}/options/{bad}").status_code == 404
    assert page.get(f"/jobs/{page.job.id + 1}/options/12-doc.html").status_code == 404  # another job's folder


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


def test_a_design_round_shows_its_doc_pick_and_questions_and_can_go_with_the_pick(golem):
    from test_flow import design_up

    room, job = asyncio.run(design_up(golem, []))
    made, choose = by_name(golem, job, "design"), plan.current(golem.store.steps(job.id))
    client = TestClient(create_app(golem.settings, golem.store, golem.scheduler), base_url="https://golem.unlaunched.dev")
    html = client.get("/").text
    assert "Three looks for Tree Cards." in html and "Field guide" in html
    assert f"<a class=button href='/jobs/{job.id}/options/{made.id}-doc.html' target=_blank rel=noopener>Open the doc ↗</a>" in html
    assert f"<img src='/jobs/{job.id}/options/{made.id}-p-1.png'" in html
    assert "<b>My pick:</b> A, with the Fraunces wordmark and the Leaf icon." in html
    assert "<ol class=questions><li>Wordmark: Fraunces or Oxanium?</li><li>Icon: Leaf or Acorn?</li></ol>" in html
    sheet = client.get(f"/steps/{choose.id}?part=sheet").text
    assert "The directions" in sheet and "Open the doc ↗" in sheet and "Its directions" in client.get(f"/steps/{made.id}?part=sheet").text

    sign_in(client)
    html = client.get("/").text
    assert "Go with your pick" in html and "Pick C" in html and "ask for another round" in html

    async def go():
        await Control(golem.store, golem.scheduler).skip(job.id, choose.id)
        assert golem.chat.texts(room)[-1] == (
            "Skipped your pick (going with mine: A, with the Fraunces wordmark and the Leaf icon), from the status page."
        )
        await golem.step()
        call = golem.harness.calls[-1]
        assert call.label == "guide" and "go with the round's" in " ".join(call.prompt.split())

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

    act(page, action="setting", slug="trees", name="mode", value="opencode-free")
    assert golem.store.project("trees").mode == "opencode-free"
    assert "no%20setting%20model" in act(page, action="setting", slug="trees", name="model", value="sonnet")  # its mode says
    act(page, action="setting", slug="trees", name="mode", value="")
    assert golem.store.project("trees").mode == ""  # back to golem's default

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
    r = page.get(f"/jobs/{page.job.id}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == f"/p/trees/jobs/{page.job.id}"  # where a job's page is now
    html = page.get(f"/p/trees/jobs/{page.job.id}").text
    assert html.count("<li class='rl done") == 4 and "<li class='rl next'>" in html and "The request" in html  # set up, spec, scaffold, Ship; mvp
    assert page.get("/jobs/99").status_code == 404 and page.get("/p/trees/jobs/99").status_code == 404
    golem.store.add_project("birds", "Birds", "")
    assert page.get(f"/p/birds/jobs/{page.job.id}").status_code == 404  # another project's job
    assert page.get("/steps/999").status_code == 404 and page.get("/jobs/1/planned/99").status_code == 404


def test_a_step_before_it_starts_can_get_its_own_model_effort_and_context(golem, page):
    sign_in(page)
    job = page.job
    assert golem.store.steps(job.id) == []  # not started: its steps are planned, not stored
    html = page.get(f"/jobs/{job.id}/planned/2?part=sheet").text  # spec
    assert "Runs on</dt><dd>opus/xhigh" in html and "default: opus" in html  # Claude power's spec
    act(page, action="step-setting", job=job.id, position=2, name="model", value="haiku")
    spec = by_name(golem, job, "spec")  # still to come: its block keeps the setting
    assert spec.model == "haiku" and golem.store.steps(job.id) == []
    act(page, action="step-setting", job=job.id, **at(spec), name="effort", value="low")
    html = page.get(sheet_of(job, spec)).text
    assert "haiku/low" in html and "model from this step" in html
    assert "spec%20gets%20that" in act(page, action="note", job=job.id, **at(spec), value="keep it to one page")
    assert "keep it to one page" in page.get(sheet_of(job, spec)).text
    assert golem.store.pending_notes(job.id) == []  # not for whichever step is next
    act(page, action="step-setting", job=job.id, **at(spec), name="model", value="")
    assert by_name(golem, job, "spec").model is None

    start(golem, job, "spec")  # written once the job gets there, with what was set for it
    spec = by_name(golem, job, "spec")
    assert (spec.id, spec.effort) != (0, None) and spec.effort == "low"
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
        await control.note(job.id, "use plain CSS", scaffold.id, scaffold.position)
        await control.step_setting(job.id, scaffold.id, scaffold.position, "model", "sonnet")
        spec = by_name(golem, job, "spec")
        await control.step_setting(job.id, spec.id, spec.position, "effort", "low")
        await golem.until("PR")
        spec_call, scaffold_call = golem.harness.calls[-2:]
        assert (spec_call.model, spec_call.effort) == ("opus", "low") and "use plain CSS" not in spec_call.prompt
        assert (scaffold_call.model, scaffold_call.effort) == ("sonnet", "medium") and "use plain CSS" in scaffold_call.prompt
        assert [r["model"] for r in golem.store.runs(job.id)] == ["opus/low", "sonnet/medium"]

    asyncio.run(go())


def test_insights_and_modes_in_settings(golem, page):
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
    html = page.get("/settings").text  # golem's modes, what each is made of
    assert "<h2>Modes</h2>" in html and "<b>Claude power</b> <span class=badge>default</span>" in html
    assert "OpenCode · models: OpenCode free · prompts: golem&#x27;s own" in html and "default-mode" not in html  # signed out

    sign_in(page)
    docs = by_name(golem, job, "docs")
    assert "sonnet/medium" in page.get(sheet_of(job, docs)).text  # Claude power's docs
    assert "OpenCode%20free%20mode" in act(page, action="default-mode", value="opencode-free")
    assert golem.store.get("default_mode") == "opencode-free"
    html = page.get("/settings").text
    assert "<option value='opencode-free' selected>OpenCode free</option>" in html and "<b>OpenCode free</b> <span class=badge>default</span>" in html
    assert "no%20mode%20called%20nope" in act(page, action="default-mode", value="nope")
    assert "Claude%20power%20mode" in act(page, action="default-mode", value="")


def test_proposals_how_often_and_thumbs_up_or_down(golem, page):
    golem.store.update_job(page.job.id, state="done")
    looked = golem.store.add_job("trees", "change", "Look at its features for things to propose", "proposals")
    golem.store.update_job(looked.id, state="done")
    card = {"gain": "Works on hikes.", "changes": ["Opens offline", "Says when it's <stale>"], "saw": "No service worker.",
            "tradeoff": "", "size": "medium", "plan": "Cache it.\nTest it."}
    offline, bigger = golem.store.add_proposals("trees", looked.id, "features", [
        ("Offline <mode>", "Works on hikes.", card),
        ("Bigger cards", "Fill the screen.\n\nWhy: small."),  # from before cards: only its body
    ])  # fmt: skip
    assert "golem proposes" not in page.get("/p/trees").text  # signed out: not shown
    sign_in(page)
    html = page.get("/p/trees").text
    assert "golem proposes" in html and "Offline &lt;mode&gt;" in html and "Fill the screen.<br><br>Why: small." in html
    assert "<p class=chips><span class=chip>features</span><span class=chip>medium</span></p><p class=gain>Works on hikes.</p>" in html
    assert "<ul class=changes><li>Opens offline</li><li>Says when it&#x27;s &lt;stale&gt;</li></ul>" in html
    assert "<p class=fact><b>Seen</b> No service worker.</p>" in html and "Trade-off" not in html  # none worth saying
    assert "<summary>How it'd be built</summary><p>Cache it.<br>Test it.</p></details>" in html
    assert html.index("Offline &lt;mode&gt;") < html.index("Bigger cards")  # oldest first
    assert html.count("name=value value=accept") == 2 and "name=proposal value='1'" in html
    assert html.count("name=value value=dismiss class='quiet dismiss'") == 2 and "Dismiss takes one off your list and tells golem nothing." in html

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

    again = golem.store.add_job("trees", "change", "Look at its polish for things to propose", "proposals")
    golem.store.update_job(again.id, state="done")
    [unused] = golem.store.add_proposals("trees", again.id, "polish", [("Sounds", "Clicks.")])
    location = act(page, action="proposal", proposal=unused.id, value="dismiss", reason="ignored")
    assert "Dismissed%20%E2%80%9CSounds%E2%80%9D" in location and golem.store.proposal(unused.id).reason == ""
    assert "Sounds" not in page.get("/p/trees/settings").text  # not among what he said: he said nothing
    assert "👎 Offline &lt;mode&gt;<span class=sub>“always online”</span>" in html and f"👍 Bigger cards <a href='/jobs/{built.id}'>" in html
    act(page, action="setting", slug="trees", name="proposals", value="hourly")
    assert golem.store.project("trees").proposals == "hourly" and "<option selected>hourly</option>" in page.get("/p/trees/settings").text
    assert golem.store.messages("trees")[-1].text != "Proposals hourly, when there's nothing else to do, from the status page."  # the flash says it
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


def test_how_many_changes_at_once_is_a_project_setting(golem, page):
    sign_in(page)
    html = page.get("/p/trees/settings").text
    assert "<h2>Changes at once</h2>" in html and "<option selected>1</option>" in html and "<option>4</option>" in html
    assert "up%20to%202%20changes%20at%20once" in act(page, action="setting", slug="trees", name="parallel_jobs", value="2")
    assert golem.store.project("trees").parallel_jobs == 2 and "<option selected>2</option>" in page.get("/p/trees/settings").text
    for bad in ("0", "5", "x", ""):
        assert "Pick%201%20to%204%20changes%20at%20once." in act(page, action="setting", slug="trees", name="parallel_jobs", value=bad)
    assert "one%20change%20at%20a%20time" in act(page, action="setting", slug="trees", name="parallel_jobs", value="1")


def test_a_project_with_two_changes_shows_each_and_who_waits_for_whom(golem, page):
    sign_in(page)
    golem.store.update_job(page.job.id, state="done")
    golem.store.update_project("trees", parallel_jobs=2)
    a = golem.store.add_job("trees", "change", "bigger cards", "feature", title="Bigger Cards")
    b = golem.store.add_job("trees", "change", "a round timer", "feature", title="Round Timer")
    for job in (a, b):
        start(golem, job, "change")
    building = next(s for s in golem.store.steps(a.id) if s.name == "change")
    golem.store.update_step(building.id, state="running", started_at="2026-10-23T10:10:00+00:00")
    golem.store.update_job(a.id, state="parked")  # between tries: it keeps its turn

    home = page.get("/").text
    assert "Bigger Cards" in home and "Round Timer" in home  # a lane each
    assert f"Waiting for job {a.id} to finish building, then make the change." in home
    talk = page.get("/p/trees").text  # each in the list, and it opens on the one ahead
    assert "Bigger Cards" in talk and "Round Timer" in talk and f"Waiting for job {a.id} to finish building" in talk
    assert "<h2>Bigger Cards</h2>" in talk
    settings = page.get("/p/trees/settings").text
    assert f"name=job value='{a.id}'" in settings and f"name=job value='{b.id}'" in settings  # controls for each



def test_changes_ready_to_ship_say_who_they_wait_for_and_who_ships_them(golem, page):
    from golem import shipments

    sign_in(page)
    golem.store.update_job(page.job.id, state="done")
    golem.store.update_project("trees", parallel_jobs=2)
    a = golem.store.add_job("trees", "change", "bigger cards", "feature", title="Bigger Cards")
    b = golem.store.add_job("trees", "change", "a round timer", "feature", title="Round Timer")
    start(golem, a, "review")
    start(golem, b, "push")  # its work's done: it waits for A's, so they ship together
    home = page.get("/").text
    assert f"Waiting for job {a.id} so they ship together." in home and "Write the docs:" not in home  # not still writing them

    start(golem, a, "push")
    shipments.form(golem.store, golem.store.job(a.id), plan.current(golem.store.steps(a.id)).id)
    home = page.get("/").text
    assert f"Shipping with job {a.id}." in home
    assert f"Stopped job {b.id}. Its work" not in act(page, action="kill", job=str(b.id))  # A hasn't merged it in yet


def test_how_many_workers_and_what_memory_each_has(golem, page, monkeypatch):
    monkeypatch.setattr(web.config, "memory_cap", lambda: 1536 * 2**20)
    assert "id=workers" not in page.get("/settings").text  # signed out: nothing to change
    sign_in(page)
    html = page.get("/settings").text
    assert "<h2>Parallel workers</h2>" in html and "<option selected>1</option>" in html and "<option>8</option>" in html
    assert "golem's memory cap is 1.5 GB, shared by golem and every worker.</p>" in html and "tight" not in html

    assert "Up%20to%202%20steps%20at%20once" in act(page, action="workers", value="2")
    assert golem.scheduler.workers == 2 and golem.store.get("workers") == "2"
    html = page.get("/settings").text
    assert "<option selected>2</option>" in html and "about 768 MB each at 2." in html and "tight" not in html
    act(page, action="workers", value="3")
    assert "about 512 MB each at 3.</p><p class=hint>That's tight" in page.get("/settings").text
    for bad in ("0", "9", "x", ""):
        assert "Pick%201%20to%208%20workers." in act(page, action="workers", value=bad)
    assert golem.scheduler.workers == 3
    assert "One%20step%20at%20a%20time." in act(page, action="workers", value="1")

    monkeypatch.setattr(web.config, "memory_cap", lambda: None)
    assert "no memory cap here" in page.get("/settings").text


def test_tags_in_settings_and_on_a_project(golem, page):
    html = page.get("/settings").text
    assert "id=tags" in html and "<b><span class=swatch></span>Game</b><span class=uses>0 projects</span>" in html and "tag-add" not in html  # signed out: a list
    sign_in(page)
    assert "Added%20the%20Dev%20Tool%20tag" in act(page, action="tag-add", value="  Dev   Tool ")
    assert [(t.name, t.color) for t in golem.store.tags()] == [("Game", 7), ("Dev Tool", 1)]  # a color no tag has
    assert "already" in act(page, action="tag-add", value="game")
    assert "A%20tag%20is" in act(page, action="tag-add", value="<b>")

    act(page, action="setting", slug="trees", name="tag", value="dev tool")
    assert golem.store.project("trees").tag == "Dev Tool"
    assert "<option selected>Dev Tool</option>" in page.get("/p/trees/settings").text
    assert "<b><span class=swatch></span>Dev Tool</b><span class=uses>1 project</span>" in page.get("/settings").text

    assert "on%20its%20projects" in act(page, action="tag-rename", name="Dev Tool", value="Developer Tool")
    assert golem.store.project("trees").tag == "Developer Tool"
    assert "already" in act(page, action="tag-rename", name="Developer Tool", value="GAME")
    act(page, action="tag-color", name="Developer Tool", value="5")
    assert golem.store.tag("developer tool").color == 5
    assert "Pick%20one" in act(page, action="tag-color", name="Developer Tool", value="9")
    assert "1%20project%20has%20none" in act(page, action="tag-delete", name="Developer Tool")
    assert golem.store.project("trees").tag == "" and [t.name for t in golem.store.tags()] == ["Game"]
    assert "No%20tag%20called" in act(page, action="setting", slug="trees", name="tag", value="Nope")
    assert "no%20tag" in act(page, action="setting", slug="trees", name="tag", value="")


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
        await golem.reply("tree-cards", "looks good")
        await golem.finish()
        assert len(golem.card_calls) == 2 and len(golem.shots) == 2

        # Deleting the project takes its card and screenshot with it.
        image = golem.settings.state_dir / "cards" / "tree-cards.jpg"
        assert image.exists()
        await golem.bot.delete(golem.store.project("tree-cards"))
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


def new(client, path="/new", **form):
    r = client.post(path, data=form, follow_redirects=False)
    return r


def test_a_new_project_is_named_on_its_form(golem, app, monkeypatch):
    from test_flow import TREES

    assert "Sign%20in" in app.get("/new", follow_redirects=False).headers["location"]
    assert "Sign%20in%20first" in new(app, name="Trees", idea="x").headers["location"]
    r = app.post("/login", data={"password": "pw", "keep": "1", "back": "/new"}, follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert "max-age=7776000" in cookie and "samesite=lax" in cookie
    html = app.get("/new").text
    assert "<option value='web_app' selected" in html and "<option>Game</option>" in html
    assert "Adopt a repo golem didn't make" in html and "github.com/unlaunched" in html
    assert "<select name=mode><option value='' selected>golem&#x27;s default</option><option value='claude-power'>Claude power</option>" in html

    r = new(app, name="", slug="Bad Slug!", idea="", mode="nope")  # what's wrong comes back with what he wrote
    html = r.text
    assert r.status_code == 400 and "Name it." in html and "Say what it&#x27;s for." in html and "An address is 2 to 31" in html
    assert "There&#x27;s no mode called nope." in html and "value='Bad Slug!'" in html and golem.store.projects() == []
    golem.workspace.repos.add("tree-cards")
    html = new(app, name="Tree Cards", idea="trees").text
    assert "tree-cards is taken, by a project or a repo. tree-cards-2 is free." in html and "value='Tree Cards'" in html

    golem.harness.script = [TREES]
    r = new(app, name="Tree Cards", slug="trees", idea="a flashcard game for identifying northeastern us trees", workflow="web_app",
            mode="opencode-free", tag="Game")  # fmt: skip
    job = golem.store.active_job("trees")
    assert r.headers["location"] == f"/p/trees/jobs/{job.id}?msg=" + quote("Started Tree Cards: Tree Flashcard Game.")  # on to its page
    project = golem.store.project("trees")
    assert (project.name, project.tag, job.mode, job.state) == ("Tree Cards", "Game", "opencode-free", "queued")
    assert app.get("/inbox", follow_redirects=False).headers["location"] == "/"  # golem's own conversation is no more
    assert "Ask%20on%20a%20project" in app.post("/say", data={"channel": "inbox", "text": "hi"}, follow_redirects=False).headers["location"]


def test_the_new_project_page_adopts_a_repo(golem, app):
    from test_adopting import ABOUT, BRIEF

    sign_in(app)
    r = new(app, "/new/adopt", repo="mimic")  # golem can't see it: says so, makes nothing
    assert r.status_code == 400 and "I didn&#x27;t adopt mimic." in r.text and "<details class='card adopt' open>" in r.text
    assert golem.store.projects() == []
    golem.workspace.existing["mimic"] = ABOUT
    golem.harness.script = [BRIEF]
    r = new(app, "/new/adopt", repo="mimic", words="cards for my table", name="", mode="auto", tag="")
    job = golem.store.active_job("mimic")
    assert r.headers["location"].startswith(f"/p/mimic/jobs/{job.id}?msg=Adopting%20Mimic") and job.request == "cards for my table"


def test_a_project_page_is_its_jobs_and_asking_always_starts_one(golem, app):
    from test_flow import TREES, by_label

    from golem.bot import QUESTION_PRIORITY

    golem.harness.script = [TREES]
    sign_in(app)
    new(app, name="Tree Cards", idea="a flashcard game for identifying northeastern us trees")
    job = golem.store.active_job("tree-cards")
    html = app.get("/p/tree-cards").text
    assert "Tree Cards" in html and "class=route-list" in html and "href='/p/tree-cards/settings'" in html
    assert "Add a note" in html and "class='card ask-form'" in html and "<option value='options'>Options</option>" in html
    parts = app.get(f"/p/tree-cards/jobs/{job.id}?parts=jobs,head,nope").json()  # what the page refreshes, a part at a time
    assert set(parts) == {"jobs", "head"} and "Tree Flashcard Game" in parts["head"] and "class=job-rows" in parts["jobs"]
    html = app.get("/p").text  # every project as a card, with its latest job's route
    assert "class=project-grid" in html and "href='/p/tree-cards'" in html and "Tree Flashcard Game" in html and "class='route small'" in html

    # With a job under way, asking still starts one of its own: it waits its turn.
    golem.harness.script = by_label({"request": [{"workflow": "change", "mode": "none", "title": "Bigger Cards"}]})
    r = app.post("/p/tree-cards/ask", data={"text": "make the cards bigger", "workflow": "", "mode": "auto"}, follow_redirects=False)
    bigger = golem.store.project_jobs("tree-cards")[0]
    assert r.headers["location"] == f"/p/tree-cards/jobs/{bigger.id}?msg=" + quote(f"Started job #{bigger.id}: Bigger Cards.")
    assert bigger.id != job.id and (bigger.workflow, bigger.state, bigger.request) == ("change", "queued", "make the cards bigger")
    assert [m.text for m in golem.store.job_messages(bigger.id)] == ["make the cards bigger"]  # its activity starts with what he asked
    golem.harness.script = by_label({"request": [{"workflow": "change", "mode": "none", "title": "A New Logo"}]})
    app.post("/p/tree-cards/ask", data={"text": "a new logo", "workflow": "options", "mode": ""})
    logo = golem.store.project_jobs("tree-cards")[0]
    assert (logo.workflow, logo.mode, logo.title) == ("options", "", "A New Logo")  # his picks over the call's
    golem.harness.script = by_label({"request": [{"workflow": "question", "mode": "none", "title": "Why So Slow"}]})
    app.post("/p/tree-cards/ask", data={"text": "why is it slow?", "workflow": "", "mode": "auto"})
    why = golem.store.project_jobs("tree-cards")[0]
    assert why.start == "look" and why.priority == QUESTION_PRIORITY  # a question goes ahead of other work
    assert "Sign%20in%20first" in app.post("/p/tree-cards/ask", data={"text": "x"}, headers={"origin": "https://evil.example"},
                                            follow_redirects=False).headers["location"]  # fmt: skip
    assert "Write%20something%20first" in app.post("/p/tree-cards/ask", data={"text": " "}, follow_redirects=False).headers["location"]
    assert "no%20mode" in app.post("/p/tree-cards/ask", data={"text": "x", "mode": "nope"}, follow_redirects=False).headers["location"]

    golem.store.update_job(logo.id, state="waiting")  # a reply, from its pane or Now, picks it back up
    act(app, action="note", job=logo.id, value="B")
    assert golem.store.job(logo.id).state == "queued" and golem.store.job_messages(logo.id)[-1].text == "B"

    # Its settings, as controls.
    html = app.get("/p/tree-cards/settings").text
    assert "name=action value='rename'" in html and "name=action value='project-slug'" in html and "action=/say" not in html
    act(app, action="rename", slug="tree-cards", value="Leaf Cards")
    assert golem.store.project("tree-cards").name == "Leaf Cards"
    assert "no%20mode" in act(app, action="setting", slug="tree-cards", name="mode", value="rigorous")
    assert golem.store.project("tree-cards").mode == ""
    assert "type%20tree-cards%20exactly" in act(app, action="project-delete", slug="tree-cards", value="trees")
    location = act(app, action="project-slug", slug="tree-cards", value="leaf-cards", back="/p/tree-cards/settings")
    assert location.startswith("/p/leaf-cards/settings?msg=Moved%20Leaf%20Cards%20to%20leaf-cards")
    for j in golem.store.project_jobs("leaf-cards"):
        golem.store.update_job(j.id, state="done")
    assert act(app, action="project-delete", slug="leaf-cards", value="leaf-cards").startswith("/p?msg=Deleted%20Leaf%20Cards")
    assert golem.store.project("leaf-cards") is None and app.get("/p/leaf-cards").status_code == 404


def test_reports_about_golem_are_for_riggy_alone(golem, page):
    job = page.job
    golem.store.add_report("instructions", "costly", "skills/deploy.md says port 80; <compose> says 8080",
                           f"lost 20 turns ({SECRET[:4]}...)", job_id=job.id, slug="trees", label="scaffold", model="opus/high")  # fmt: skip
    for n in range(3):
        golem.store.file_incident("f1", "environment", "blocked" if n == 2 else "minor", "merge failed: GraphQL: Something went wrong",
                                  "the whole output", job_id=job.id, slug="trees", label="merge", version="abc1234def")  # fmt: skip
    html = page.get("/reports").text
    assert "Sign in" in html and "skills/deploy.md" not in html  # they quote runs and errors

    sign_in(page)
    html = page.get("/reports").text  # the new ones, both sources
    assert "skills/deploy.md says port 80; &lt;compose&gt; says 8080" in html and "merge failed: GraphQL" in html
    assert "<span class='badge trouble'>blocked</span><b>environment</b>" in html and "3 times since" in html
    assert f"<a href='/p/trees/jobs/{job.id}'>job #{job.id}</a>" in html and "golem abc1234" in html and "opus/high" in html
    assert "<pre class=log>the whole output</pre>" in html
    html = page.get("/reports?source=agent").text
    assert "skills/deploy.md" in html and "merge failed" not in html
    golem.store.db.execute("UPDATE reports SET state = 'dismissed' WHERE fingerprint = 'f1'")
    assert "merge failed" not in page.get("/reports").text and "merge failed" in page.get("/reports?state=dismissed").text
    assert "No reports here." in page.get("/reports?state=accepted").text
    assert "href=/reports>Reports</a>" in page.get("/settings").text


def test_riggy_takes_a_fix_golem_proposes_to_itself_or_turns_it_down(golem, page):
    from golem.store import INTERNAL, TRIAGE

    golem.store.internal_project()
    job = golem.store.add_job(INTERNAL, "change", "Triage golem's reports up to #3", "triage", start=TRIAGE)
    a = golem.store.add_report("instructions", "costly", "skills/deploy.md says port 80", "lost 20 turns", job_id=page.job.id,
                               slug="trees", label="scaffold")  # fmt: skip
    b = golem.store.file_incident("f1", "environment", "blocked", "merge failed: GraphQL", "> the whole output", job_id=page.job.id,
                                  slug="trees", label="merge")  # fmt: skip
    c = golem.store.add_report("wish", "minor", "a way to see CI logs")
    made = [("Fix the port in skills/deploy.md", "Problem: workers lose turns.\n\nChange: say 8080.", "instructions", [a.id]),
            ("Back off on GitHub errors", "Change: retry later.", "code", [b.id])]  # fmt: skip
    fix, backoff = golem.store.add_triaged(job.id, c.id, made, {c.id: "nice to have"})

    assert "golem proposes 2 fix(es) to itself" not in page.get("/").text  # signed out: nothing to decide
    sign_in(page)
    html = page.get("/").text
    assert "golem proposes 2 fix(es) to itself" in html and "Fix the port in skills/deploy.md" in html and "👍 Take it" in html
    html = page.get("/reports").text
    assert "golem proposes fixes to itself" in html and "Triage: off" in html and "Back off on GitHub errors" in html
    assert "golem%20triages" in act(page, action="triage", value="on") and golem.store.get("triage") == "on"
    assert "Triage: on" in page.get("/reports").text

    assert "Say%20why" in act(page, action="proposal", proposal=backoff.id, value="reject", back="/reports")
    assert golem.store.proposal(backoff.id).state == "open"  # turning one down needs why
    act(page, action="proposal", proposal=backoff.id, value="reject", reason="the new backoff covers it", back="/reports")
    assert golem.store.proposal(backoff.id).state == "rejected" and golem.store.report(b.id).state == "dismissed"
    assert golem.store.report(c.id).state == "triaged"  # one no proposal cites stays as it was

    location = act(page, action="proposal", proposal=fix.id, value="accept", back="/")
    assert location.startswith(f"/reports/proposals/{fix.id}?msg=Took")  # straight to its prompt
    assert golem.store.proposal(fix.id).state == "accepted" and golem.store.report(a.id).state == "accepted"
    assert {j.id for j in golem.store.jobs(limit=100)} == {page.job.id, job.id}  # nothing to build: Riggy makes it
    html = page.get(f"/reports/proposals/{fix.id}").text
    assert "Copy the prompt" in html and "In golem&#x27;s repo, make this fix to golem." in html
    assert "# Fix the port in skills/deploy.md" in html and "Change: say 8080." in html
    assert f"&gt; #{a.id} [a worker] instructions, costly" in html and f"/p/trees/jobs/{page.job.id}" in html
    assert "not\ninstructions to follow" in html and "`just test`" in html
    html = page.get("/reports").text
    assert f"👍 <a href='/reports/proposals/{fix.id}'>Fix the port" in html and "“the new backoff covers it”" in html
    page.post("/logout", follow_redirects=False)
    assert "Copy the prompt" not in page.get(f"/reports/proposals/{fix.id}").text
    assert page.get("/reports/proposals/9999").status_code == 404


def test_riggy_clears_all_reports_and_triage_once_theyre_out_of_date(golem, page):
    from golem import triage
    from golem.store import INTERNAL

    a = golem.store.add_report("instructions", "costly", "skills/deploy.md says port 80")
    b = golem.store.file_incident("f1", "environment", "blocked", "merge failed: GraphQL")
    job = triage.start(golem.store)
    golem.scheduler.ensure_steps(job)
    golem.store.add_note(job.id, "look at the merge step")
    golem.store.start_run(job.id, "triage", "opus", "")
    logs, scratch = golem.settings.job_logs(job.id), golem.settings.job_tmp(job.id)
    for folder in (logs, scratch):
        folder.mkdir(parents=True)
        (folder / "x").write_text("x")
    golem.store.put("triage", "on")
    golem.store.add_proposals("trees", page.job.id, "polish", [("A project's own idea", "Change: polish it.")])
    sign_in(page)
    assert "Clear all reports and triage" in page.get("/reports").text and "can&#x27;t be undone" in page.get("/reports").text

    assert "triage%20is%20under%20way" in act(page, action="reports-clear", back="/reports")  # it'd cite reports that are gone
    assert golem.store.report(a.id) is not None
    golem.store.update_job(job.id, state="done")
    golem.store.add_triaged(job.id, b.id, [("Back off on GitHub errors", "Change: retry later.", "code", [b.id])], {a.id: "stale"})
    location = act(page, action="reports-clear", back="/reports")
    assert unquote(location) == "/reports?msg=Cleared 2 report(s), 1 proposal(s) for golem and 1 triage job(s)."
    assert golem.store.reports() == [] and golem.store.proposals(INTERNAL) == [] and golem.store.jobs_from("triage") == []
    assert golem.store.steps(job.id) == [] and golem.store.runs(job.id) == [] and not logs.exists() and not scratch.exists()
    assert [p.title for p in golem.store.proposals("trees")] == ["A project's own idea"]  # projects' proposals aren't golem's
    assert golem.store.get("triage") == "on" and golem.store.job(page.job.id) is not None
    html = page.get("/reports").text
    assert "No reports here." in html and "Back off on GitHub errors" not in html and "Lately:" not in html
    assert "nothing%20to%20clear" in act(page, action="reports-clear", back="/reports")


def test_golems_own_project_is_never_shown_or_changed(golem, page):
    import asyncio

    from golem import nuke, projectpage
    from golem.store import INTERNAL

    golem.store.internal_project()
    golem.store.internal_project()  # made once
    triage = golem.store.add_job(INTERNAL, "change", "Triage golem's reports", "triage")
    assert [p.slug for p in golem.store.projects()] == ["trees"] and golem.store.project(INTERNAL).name == "golem"
    assert projectpage.href(triage) == "/reports" and page.get(f"/jobs/{triage.id}", follow_redirects=False).headers["location"] == "/reports"
    sign_in(page)
    for path in ("/", "/p", "/settings"):
        assert "its jobs about how golem works" not in page.get(path).text and "/p/golem" not in page.get(path).text, path
    for path in ("/p/golem", "/p/golem/settings", "/p/golem/jobs", f"/p/golem/jobs/{triage.id}", "/p/golem/history"):
        assert page.get(path).status_code == 404, path
    assert page.post("/p/golem/ask", data={"text": "hi"}).status_code == 404
    assert "No%20project%20called%20golem" in act(page, action="pause", slug=INTERNAL)
    assert "No%20project%20called%20golem" in act(page, action="rename", slug=INTERNAL, value="Mine")
    assert "isn%27t%20there" in act(page, action="project-delete", slug=INTERNAL, value=INTERNAL)
    assert golem.store.project(INTERNAL).name == "golem" and not golem.store.project(INTERNAL).paused
    said = asyncio.run(nuke.plan(golem.settings, golem.store, golem.deployer))
    assert "- 1 project(s)" in said and "    trees (Trees <b>)" in said and "golem (golem)" not in said


def test_every_page_is_a_click_from_anywhere(golem, page):
    golem.store.add_project("birds", "Birds", "")
    golem.store.update_job(page.job.id, state="waiting")
    sign_in(page)
    for path in ("/", "/p", "/settings", "/insights", "/reports", "/new", "/p/trees", "/p/trees/jobs", "/p/trees/settings", f"/jobs/{page.job.id}"):
        html = page.get(path).text
        assert "<nav class=tabs" in html and "<nav class=side" in html, path  # the tab bar, and the sidebar on a wide screen
        assert "href='/p/birds/settings'" in html and "href='/settings'" in html, path  # another project's settings, golem's
        assert "class=count aria-label='1 needs you'>1</b>" in html, path  # trees is waiting on him
    html = page.get("/p/trees/settings").text  # a project's own tabs: its jobs, all of them, and settings
    assert ("<a href='/p/trees'>Jobs</a><a href='/p/trees/jobs'>All jobs</a>"
            "<a href='/p/trees/settings' class=on aria-current=page>Settings</a></nav>") in html  # fmt: skip
    assert "<li class='c1 on'><a href='/p/trees'>" in html  # lit up in the sidebar
    assert "href=/new" in html and "href='/workflows'" not in html  # a new project, for Riggy; no Workflows tab
    assert "class=gear href='/p/birds/settings'" in page.get("/p").text  # straight to its settings from the list
    page.post("/logout", follow_redirects=False)
    html = page.get("/p/trees").text
    assert "class=gear" not in html and "href='/p/trees/settings'" not in html  # signed out: no settings to go to


def test_a_long_conversation_shows_its_latest_messages_first(golem, app):
    from golem.web import SHOWN

    golem.store.add_project("trees", "Trees", "")
    for n in range(SHOWN + 5):
        golem.store.add_message("trees", "golem", f"note {n}.")
    sign_in(app)
    assert "href='/p/trees/history'>History</a>" in app.get("/p/trees").text  # said before jobs had their own activity
    html = app.get("/p/trees/history").text
    assert f"note {SHOWN + 4}." in html and "note 5." in html and "note 4." not in html
    assert f"href='?show={2 * SHOWN}' data-earlier>Show earlier messages" in html
    html = app.get(f"/p/trees/history?part=lanes&show={2 * SHOWN}").text  # what the link loads in place
    assert "note 0." in html and "Show earlier" not in html
    for n in range(SHOWN + 1):
        golem.store.add_message("status", "golem", f"news {n}.")
    html = app.get("/").text  # golem's news, its latest, on Now
    assert f"news {SHOWN}." in html and "news 0." not in html


def test_conversations_are_private_and_writing_needs_this_site(golem, app):
    from test_flow import TREES

    golem.harness.script = [TREES]
    sign_in(app)
    new(app, name="Tree Cards", idea="a flashcard game for identifying northeastern us trees")
    evil = {"origin": "https://evil.example"}
    r = app.post("/act", data={"action": "project-delete", "slug": "tree-cards", "value": "tree-cards"}, headers=evil, follow_redirects=False)
    assert "Sign%20in%20first" in r.headers["location"] and golem.store.project("tree-cards") is not None
    r = app.post("/new", data={"name": "Evil", "idea": "x"}, headers=evil, follow_redirects=False)
    assert "Sign%20in%20first" in r.headers["location"] and golem.store.project("evil") is None
    app.post("/logout", follow_redirects=False)
    html = app.get("/p/tree-cards").text
    assert "Sign in (Settings) to see what golem and you said" in html and "class='card ask-form'" not in html
    assert "/p/tree-cards/history" not in html and "Sign%20in" in app.get("/p/tree-cards/history", follow_redirects=False).headers["location"]
    assert "You said:" not in app.get("/p").text and "Tree Cards" in app.get("/p").text


# --- the installable app and notifications ----------------------------------------


def test_installable_with_its_own_icon_and_service_worker(app):
    manifest = app.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["display"] == "standalone" and manifest.json()["start_url"] == "/"
    icon = app.get("/icon-180.png")
    assert icon.content.startswith(b"\x89PNG") and icon.content[16:24] == (180).to_bytes(4, "big") * 2
    assert app.get("/icon-64.png").status_code == 404 and app.get("/icon-64-light.png").status_code == 404
    # The mark on ember: granite at the top-left, brick at the bottom-right, the forehead in the
    # mark's color and an eye see-through. The light one for light-mode phones, chosen as the page loads.
    for path, top_left, bottom_right, ink in (("/icon-180.png", "4a4541", "8a3a2a", "ece8e1"), ("/icon-180-light.png", "f8f5f1", "d2876a", "2a2826")):
        rows = _pixels(app.get(path).content)
        assert _near(rows[0][0], top_left) and _near(rows[179][179], bottom_right)
        assert _near(rows[41][90], ink) and not _near(rows[67][72], ink)
    assert "/icon-180-light.png" in app.get("/").text
    sw = app.get("/sw.js")
    assert sw.headers["content-type"].startswith("text/javascript") and "showNotification" in sw.text
    assert "apple-touch-icon" in app.get("/").text and "serviceWorker.register" not in app.get("/").text  # signed out
    sign_in(app)
    assert "serviceWorker.register" in app.get("/").text and "Turn on for this device" in app.get("/settings").text


def test_a_changed_icon_gets_a_new_url_so_no_cache_hands_out_the_old_one(app):
    # Cloudflare, in front of golem, kept serving the old icon at /icon-180.png for a week.
    v = pwa.version()
    page = app.get("/").text
    assert f"href='/icon-180.png?v={v}'" in page and f"'/icon-180-light.png?v={v}'" in page
    assert [i["src"] for i in app.get("/manifest.webmanifest").json()["icons"]] == [f"/icon-192.png?v={v}", f"/icon-512.png?v={v}"]
    assert f"icon: '/icon-192.png?v={v}'" in app.get("/sw.js").text
    assert "immutable" in app.get(f"/icon-180-light.png?v={v}").headers["cache-control"]
    for elsewhere in ("/icon-180.png", "/icon-180.png?v=0ld"):
        assert app.get(elsewhere).headers["cache-control"] == "public, max-age=3600"


def _pixels(png: bytes) -> list[list[bytes]]:
    """An RGB PNG's pixels by row, as pwa.icon writes them (no filters)."""
    size, data, i = int.from_bytes(png[16:20], "big"), b"", 8
    while i < len(png):
        n = int.from_bytes(png[i : i + 4], "big")
        data += png[i + 8 : i + 8 + n] if png[i + 4 : i + 8] == b"IDAT" else b""
        i += 12 + n
    raw, stride = zlib.decompress(data), 1 + 3 * size
    return [[raw[r * stride + 1 + 3 * x : r * stride + 4 + 3 * x] for x in range(size)] for r in range(size)]


def _near(pixel: bytes, color: str, within: int = 6) -> bool:
    return all(abs(p - c) <= within for p, c in zip(pixel, bytes.fromhex(color)))


def test_wears_its_mark(app):
    html = app.get("/").text
    assert "<title>Golem</title>" in html and html.count("aria-label=Golem>") == 2  # the header's lockup and the sidebar's
    assert "<title>Settings · Golem</title>" in app.get("/settings").text
    icon = app.get("/favicon.svg")
    assert icon.headers["content-type"].startswith("image/svg+xml") and "<linearGradient" in icon.text  # the app icon, small
    assert app.get("/manifest.webmanifest").json()["name"] == "Golem"


def test_serves_its_own_fonts_and_nothing_else_from_there(app):
    for name in ("plex-sans.woff2", "oxanium.woff2"):
        r = app.get(f"/fonts/{name}")
        assert r.status_code == 200 and r.content.startswith(b"wOF2") and r.headers["content-type"] == "font/woff2"
        assert "immutable" in r.headers["cache-control"]
    assert app.get("/fonts/OFL-Oxanium.txt").status_code == 404
    assert app.get("/fonts/..%2Fweb.py").status_code == 404


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
    assert payload == {"title": "golem", "body": "Question: photos or drawings?", "url": "/", "tag": "inbox"}
    # About a job: tapping it opens the job, at the step it's about.
    golem.store.add_project("trees", "Trees", "s")
    job = golem.store.add_job("trees", "change", "r", "feature")
    asyncio.run(golem.talk.send("trees", "Which photos?", job=job.id, step=7))
    assert sent[-1][1] == {"title": "Trees", "body": "Which photos?", "url": f"/p/trees/jobs/{job.id}#/steps/7", "tag": f"job-{job.id}"}
    assert golem.store.job_messages(job.id)[-1].text == "Which photos?"
    assert [s["endpoint"] for s in golem.store.subscriptions()] == ["https://push.example/phone"]  # the gone one's forgotten
    assert app.post("/push/test", json={}).json() == {"sent": 1}
    app.post("/push/unsubscribe", json={"endpoint": "https://push.example/phone"})
    assert golem.store.subscriptions() == []


# --- workflows, drawn ---------------------------------------------------------------


def test_a_job_page_lists_its_steps_and_one_from_before_workflows_shows_the_way_it_went(golem, page):
    job = page.job
    start(golem, job, "mvp")
    html = page.get(f"/p/trees/jobs/{job.id}").text
    assert "steps-card" in html and "class=flow " not in html and "view-toggle" not in html and "/static/flow.js" not in html
    assert page.get(f"/jobs/{job.id}/flow.json").status_code == 404
    golem.store.update_job(job.id, state="done", graph="")  # a job from before workflows has no copy
    html = page.get(f"/p/trees/jobs/{job.id}").text
    assert "The way it went" in html and "class=trail" in html


def test_a_job_from_checklist_mode_still_shows_its_steps_and_golem_passes_over_any_left(golem, page):
    """Checklist mode is gone; cl-trees' jobs and their items stay (steps.item, unused)."""
    job = page.job
    start(golem, job, "mvp")
    item = json.dumps({"plan": 1, "n": 1, "of": 2, "try": 1, "title": "File A", "check": "test -f a.txt"})
    for i, (kind, name, state) in enumerate((("agent", "item", "done"), ("check", "check", "done"), ("agent", "item", "pending"), ("check", "check", "pending"))):
        golem.store.db.execute("INSERT INTO steps (job_id, position, kind, name, state, item, links) VALUES (?, ?, ?, ?, ?, ?, '{}')",
                               (job.id, 100 + i, kind, name, state, item))  # fmt: skip
    steps = golem.store.steps(job.id)
    sign_in(page)
    assert page.get(f"/p/trees/jobs/{job.id}").status_code == 200
    for s in steps[-4:]:
        assert page.get(f"/steps/{s.id}").status_code == 200
    project = golem.store.project("trees")
    for s in steps[-2:]:  # left in a job under way: passed over, never run
        outcome = asyncio.run(getattr(golem.scheduler, f"do_{s.kind}")(job, project, s))
        assert (outcome.state, outcome.summary) == ("skipped", "Checklist mode is gone: passed over")


def test_there_is_no_workflow_editor_and_modes_are_in_settings(golem, page):
    sign_in(page)
    for path in ("/workflows", "/workflows/1", "/workflows/1.json", "/static/flow.js", "/static/editor.js"):
        assert page.get(path).status_code == 404, path
    assert page.post("/workflows/1/save", json={}).status_code in (404, 405)
    html = page.get("/settings").text
    assert "id=modes" in html and "/settings/blocks" not in html and "href='/workflows'" not in html
    assert page.get("/settings/blocks").status_code == 404  # what blocks say is a mode's prompt set now


def test_a_project_page_opens_on_what_needs_riggy_else_whats_running_else_the_latest_change(golem, page):
    from golem.projectpage import chosen

    trees = page.job
    golem.store.update_job(trees.id, state="done")
    looked = golem.store.add_job("trees", "change", "Look at its features", "proposals", start="propose")
    golem.store.update_job(looked.id, state="done")
    newest_first = lambda: golem.store.project_jobs("trees")  # noqa: E731
    assert chosen(newest_first()).id == trees.id  # nothing under way: the latest that changed it, not the look
    assert "<span class='badge finished'>Done</span>" in page.get(f"/p/trees/jobs/{looked.id}").text  # a look ships nothing
    a = golem.store.add_job("trees", "change", "bigger cards", "feature")
    b = golem.store.add_job("trees", "change", "a timer", "feature")
    assert chosen(newest_first()).id == a.id  # the next to run
    golem.store.update_job(b.id, state="running")
    assert chosen(newest_first()).id == b.id
    golem.store.update_job(a.id, state="waiting")
    assert chosen(newest_first()).id == a.id  # what needs him first
    assert "/p/trees/history" not in page.get("/p/trees").text  # nothing said before jobs had their own activity


def test_a_project_page_lists_its_latest_jobs_and_all_of_them_are_a_search_away(golem, page, monkeypatch):
    from golem import jobhistory
    from golem.projectpage import JOBS_SHOWN

    trees = page.job  # the oldest, and still queued
    made = [golem.store.add_job("trees", "change", f"change number {i}", "feature") for i in range(JOBS_SHOWN + 3)]
    for j in made:
        golem.store.update_job(j.id, state="done")
    golem.store.update_job(made[3].id, title="Bigger Leaves")
    golem.store.update_job(made[7].id, title="Night Mode", request="make it dark at night\nand use the owl palette")
    golem.store.update_job(made[8].id, state="failed")

    html = page.get("/p/trees").text  # the latest, and one still under way however old
    assert html.count("class='job-row") == JOBS_SHOWN + 1 and f"/p/trees/jobs/{trees.id}'" in html
    assert f"/p/trees/jobs/{made[0].id}'" not in html and "href='/p/trees/jobs'>Search all 16 jobs ›</a>" in html

    def found(query: str = "") -> str:
        r = page.get(f"/p/trees/jobs{query}")
        assert r.status_code == 200
        return r.text

    html = found()  # anyone can look
    assert html.count("class=job-row>") == 16 and "<p class=hint>16 jobs</p>" in html
    assert "<a href='/p/trees/jobs' class=on aria-current=page>All jobs</a>" in html and "class=clear" not in html
    html = found("?q=bigger+LEAVES")  # every word, in its title or request, any case
    assert html.count("class=job-row>") == 1 and "Bigger Leaves" in html and "1 job of 16" in html and "class=clear" in html
    html = found("?q=owl")  # in its request: where, shown
    assert "Night Mode" in html and "<p class=excerpt>make it dark at night and use the owl palette</p>" in html
    html = found(f"?q=%23{made[2].id}")  # its number
    assert html.count("class=job-row>") == 1 and f"/p/trees/jobs/{made[2].id}'" in html
    html = found("?state=active")
    assert html.count("class=job-row>") == 1 and "<span class='badge queued'>Queued</span>" in html and "<option value='active' selected>" in html
    assert found("?state=failed").count("class=job-row>") == 1 and "No jobs match." in found("?state=killed")
    html = found("?workflow=web_app")  # the workflows its jobs ran, even from before workflows
    assert html.count("class=job-row>") == 1 and f"/p/trees/jobs/{trees.id}'" in html and "<option value='change'>" in html
    assert found("?state=nope").count("class=job-row>") == 16
    assert "value='&lt;b&gt;'" in found("?q=<b>")

    monkeypatch.setattr(jobhistory, "PER_PAGE", 5)  # a page at a time, keeping the search
    html = found()
    assert "1–5 of 16 jobs" in html and "href='/p/trees/jobs?page=2'>Older ›" in html and "Newer" not in html
    html = found("?state=done&page=2")
    assert "6–10 of 14 found" in html and "href='/p/trees/jobs?state=done'>‹ Newer" in html and "?state=done&amp;page=3'>Older" in html
    assert "16–16 of 16" in found("?page=99") and "Older" not in found("?page=99")
    parts = page.get("/p/trees/jobs?q=owl&parts=found,nope").json()  # what its refresh asks for: the search kept
    assert set(parts) == {"found"} and "Night Mode" in parts["found"] and "Bigger Leaves" not in parts["found"]
    assert page.get("/p/nope/jobs").status_code == 404


