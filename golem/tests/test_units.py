"""Smaller pieces: playbooks, intake, the harness's reading of Claude Code output, the deploy CLI."""

from __future__ import annotations

import asyncio
import gzip
import json
import stat
import sys

import httpx
import pytest
from conftest import REPO

from golem import config, deploy_cli, intake, options
from golem import playbook as playbooks
from golem.config import Settings
from golem.deployer_client import DeployerClient, DeployerError
from golem.harness import ClaudeHarness, interpret
from golem.store import EFFORTS, MODELS, Step, Store

# --- settings -----------------------------------------------------------------


def test_settings_need_a_domain_and_an_org():
    for env in ({"GITHUB_ORG": "me"}, {"BASE_DOMAIN": "example.com"}):
        with pytest.raises(SystemExit, match="BASE_DOMAIN and GITHUB_ORG must be set"):
            Settings.from_env(env)


def test_settings_need_a_web_password():
    for password in ({}, {"GOLEM_WEB_PASSWORD": ""}):
        with pytest.raises(SystemExit, match="GOLEM_WEB_PASSWORD must be set"):
            Settings.from_env({"BASE_DOMAIN": "example.com", "GITHUB_ORG": "me", **password})


@pytest.mark.parametrize("machine, platform", [("aarch64", "linux/arm64"), ("arm64", "linux/arm64"), ("x86_64", "linux/amd64")])
def test_project_images_are_built_for_this_box(monkeypatch, machine, platform):
    env = {"BASE_DOMAIN": "example.com", "GITHUB_ORG": "me", "GOLEM_WEB_PASSWORD": "pw"}
    monkeypatch.setattr(config.platform, "machine", lambda: machine)
    assert Settings.from_env(env).platform == platform
    assert Settings.from_env({**env, "GOLEM_PLATFORM": "linux/riscv64"}).platform == "linux/riscv64"


# --- playbooks ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name, mode, agents",
    [
        ("reckless", "reckless", ["build"]),
        ("standard", "standard", ["spec", "scaffold", "mvp", "buildout", "review", "docs"]),
        ("rigorous", "rigorous", ["spec", "spec-review", "scaffold", "mvp", "buildout", "review", "second-pass", "docs"]),
        ("feature", "reckless", ["change", "docs"]),
        ("feature", "standard", ["change", "review", "docs"]),
        ("feature", "rigorous", ["change", "review", "second-pass", "docs"]),
        ("options", "reckless", ["options", "change", "docs"]),
        ("options", "standard", ["options", "change", "review", "docs"]),
    ],
)
def test_playbooks_load(name, mode, agents):
    book = playbooks.load(REPO / "playbooks" / f"{name}.md")
    steps = book.for_mode(mode)
    assert [s.name for s in steps if s.kind == "agent"] == agents
    assert all(s.instructions and s.max_turns > 0 for s in steps if s.kind == "agent")
    assert [s.kind for s in steps][0] == "setup" and [s.kind for s in steps][-2:] == ["deploy", "alert"]
    assert book.intro


@pytest.mark.parametrize("name, agent, last", [("question", "examine", "answer"), ("proposals", "propose", "proposals")])
def test_playbooks_that_only_look_load_in_every_mode(name, agent, last):
    book = playbooks.load(REPO / "playbooks" / f"{name}.md")
    for mode in ("reckless", "standard", "rigorous"):
        assert [(s.kind, s.name) for s in book.for_mode(mode)] == [("refresh", "refresh"), ("agent", agent), (last, last)]


def test_proposals_are_read_from_a_verdict_and_shown_with_what_riggy_said():
    from golem.proposals import MAX_PER_RUN, collect, past
    from golem.store import Proposal

    raw = [
        {"title": "  Offline   mode ", "what": "Cache the deck in a service worker.", "why": "Hikes have no signal.", "size": "medium"},
        {"title": "", "what": "untitled"},  # dropped
        "not a proposal",
        {"title": "Bigger cards", "what": "Fill the screen on phones.", "why": "", "size": "huge"},
        *[{"title": f"Extra {i}", "what": "x", "why": "y", "size": "small"} for i in range(5)],
    ]
    found = collect(raw)
    assert len(found) == MAX_PER_RUN and collect("nonsense") == []
    assert found[0] == ("Offline mode", "Cache the deck in a service worker.\n\nWhy: Hikes have no signal.\n\nSize: medium.")
    assert found[1] == ("Bigger cards", "Fill the screen on phones.")  # no why, and no size it can't use

    def made(title, state, reason=""):
        return Proposal(1, "t", 1, "polish", title, "body", state, reason, None, "2026-10-01T00:00:00+00:00", None)

    listed = past([made("Dark mode", "rejected", "it's a  daytime\napp"), made("Streaks", "accepted"), made("Sounds", "open")])
    assert listed.splitlines() == [
        "- [polish] Dark mode — turned down: “it's a daytime app”",
        "- [polish] Streaks — accepted",
        "- [polish] Sounds — not decided yet",
    ]
    assert past([]) == "(none yet: these are the first)"


@pytest.mark.parametrize(
    "steps, sections, error",
    [
        ("[setup, {agent: a, turns: 5}, deploy, alert]", "## b\nstuff", "agent step a has no '## a' section"),
        ("[setup, {agent: a, turns: 5}, deploy, alert]", "## a\nx\n\n## b\nstuff", "sections without an agent step: b"),
        ("[setup, {agent: a, turns: 5}, deploy]", "## a\nx", "must start with setup and end with deploy, alert"),
        ("[{agent: a, turns: 5}, deploy, alert]", "## a\nx", "must start with setup and end with deploy, alert"),
        ("[setup, {agent: a, turns: 5}, deploy, {do: alert, modes: [rigorous]}]", "## a\nx", "must start with setup"),
        ("[setup, {agent: a, turns: 5}, ship-it, deploy, alert]", "## a\nx", "unknown step 'ship-it'"),
        ("[setup, {agent: a, turns: 5, modes: [yolo]}, deploy, alert]", "## a\nx", "not yolo"),
        ("[setup, {agent: a, turns: 5}, {do: merge, optional: true}, deploy, alert]", "## a\nx", "merge can't be optional"),
    ],
)
def test_bad_playbooks_are_refused(tmp_path, steps, sections, error):
    path = tmp_path / "bad.md"
    path.write_text(f"---\nsteps: {steps}\n---\nintro\n\n{sections}\n")
    with pytest.raises(ValueError, match=error):
        playbooks.load(path)


# --- intake -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, slug",
    [("Tree Cards", "tree-cards"), ("  Hello, World!! ", "hello-world"), ("x", "xapp"), ("", "project"), ("a" * 40, "a" * 31)],
)
def test_slugify(text, slug):
    assert intake.slugify(text) == slug
    assert intake.valid_slug(slug)


@pytest.mark.parametrize("slug", ["golem", "-x", "x", "ab-", "UPPER", "a_b", "a" * 32])
def test_invalid_slugs(slug):
    assert not intake.valid_slug(slug)


def test_unique_slug():
    taken = {"trees", "trees-2", "golem-app-2"}

    async def is_taken(slug):
        return slug in taken

    assert asyncio.run(intake.unique_slug("oaks", is_taken)) == "oaks"
    assert asyncio.run(intake.unique_slug("trees", is_taken)) == "trees-3"
    assert asyncio.run(intake.unique_slug("golem", is_taken)) == "golem-app-3"


# --- options ------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x86\x00\x00\x03\x4c rest of it"  # 390 x 844


def test_collect_keeps_files_in_the_checkout_and_copies_the_first_pictures(tmp_path):
    checkout, dest = tmp_path / "repo", tmp_path / "state"
    a = checkout / "docs/options/job-7/a"
    a.mkdir(parents=True)
    for name in ("1.png", "2.PNG", "3.jpg", "4.webp"):
        (a / name).write_bytes(PNG)
    (a / "hero.css").write_text("body {}")
    (a / "huge.png").write_bytes(b"x" * (options.MAX_IMAGE_BYTES + 1))
    (tmp_path / "secret.png").write_bytes(PNG)
    (a / "link.png").symlink_to(tmp_path / "secret.png")
    (checkout / ".git").mkdir()
    (checkout / ".git/config.png").write_bytes(PNG)
    raw = [
        {"title": "Round", "description": "Soft.", "files": [
            "docs/options/job-7/a/huge.png", "docs/options/job-7/a/1.png", "docs/options/job-7/a/hero.css", "../secret.png",
            "docs/options/job-7/a/link.png", ".git/config.png", "docs/options/job-7/a/2.PNG", "docs/options/job-7/a/3.jpg",
            "docs/options/job-7/a/4.webp", "docs/options/job-7/a/missing.png",
        ]},
        "nonsense",
        {"title": "", "description": "No files.", "files": []},
    ]  # fmt: skip
    made = options.collect(raw, checkout, dest, "12")
    assert [(o.letter, o.title) for o in made] == [("A", "Round"), ("B", "Option B")]
    a_files = ["huge.png", "1.png", "hero.css", "2.PNG", "3.jpg", "4.webp"]
    assert made[0].files == [f"docs/options/job-7/a/{name}" for name in a_files]
    assert made[0].images == ["12-a-1.png", "12-a-2.png", "12-a-3.jpg"]  # the first three that aren't too big
    assert (dest / "12-a-2.png").read_bytes() == PNG and sorted(p.name for p in dest.iterdir()) == made[0].images
    assert made[0].where == "docs/options/job-7/a/" and made[1].where == ""
    assert all(options.is_image(name) for name in made[0].images)
    assert not any(options.is_image(name) for name in ("../12-a-1.png", "12-a-1.svg", "golem.db", "x-a-1.png"))


@pytest.mark.parametrize(
    "reply, more, picked",
    [
        ("B", False, ["B"]),
        ("b.", False, ["B"]),
        ("Option c", False, ["C"]),
        ("B, but bigger eyes", False, ["B"]),
        ("A mix of B and C", False, []),  # "A" the word, not the option: the next step reads it all
        ("E", False, []),  # not one of them
        ("none of these, try more pastel", True, []),
        ("show me some other options", True, []),
        ("the pastel one but with more contrast", False, []),
    ],
)
def test_replies_to_options_without_a_model(reply, more, picked):
    assert options.plain_reply(reply, ["A", "B", "C"]) == options.Reply(more, picked)


@pytest.mark.parametrize(
    "text, wants",
    [
        ("i'd like to decide on a new art style, show me 3 that are more chibi", True),
        ("give me some options for the logo and I'll pick", True),
        ("mock up a couple of layouts so I can choose", True),
        ("make the hero more chibi", False),
        ("add a quiz mode", False),
    ],
)
def test_asking_for_options_without_a_model(text, wants):
    assert options.asks_for_options(text) is wants


def test_what_a_pick_tells_the_next_step():
    found = [options.Option("A", "Round", "", ["docs/o/a/1.png", "docs/o/a/x.css"], []), options.Option("B", "Ink", "", ["docs/o/b.png"], [])]
    assert options.picked(options.Reply(False, ["B"]), found, "b please") == "You picked B (Ink). You said: “b please”. Files: docs/o/b.png."
    assert options.picked(options.Reply(False, ["A", "B"]), found, "") == (
        "You picked A (Round) and B (Ink). Files: A in docs/o/a/; B in docs/o/b.png."
    )
    assert options.picked(options.Reply(False, []), found, "you choose") == (
        "You didn't pick one option, so the next step decides from what you said. You said: “you choose”."
    )


# --- reading Claude Code's result ---------------------------------------------


def result(**fields):
    return {"type": "result", "subtype": "success", "is_error": False, "session_id": "s", "num_turns": 4,
            "total_cost_usd": 0.5, "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 7},
            **fields}  # fmt: skip


def test_interpret_verdict():
    r = interpret(result(structured_output={"verdict": "done", "message": "hi"}), None, 0)
    assert (r.outcome, r.verdict, r.session_id) == ("verdict", {"verdict": "done", "message": "hi"}, "s")
    assert r.usage == {"num_turns": 4, "cost_usd": 0.5, "input_tokens": 100, "output_tokens": 7}


@pytest.mark.parametrize(
    "fields, outcome",
    [
        ({"subtype": "error_max_turns", "is_error": True}, "max_turns"),
        ({"is_error": True, "result": "Claude usage limit reached. Your limit will reset at 5pm"}, "limit"),
        ({"is_error": True, "api_error_status": 429, "result": "x"}, "limit"),
        ({"is_error": True, "result": "API Error: 500"}, "error"),
        ({"structured_output": {"verdict": "maybe", "message": ""}}, "error"),
        ({}, "error"),
    ],
)
def test_interpret_failures(fields, outcome):
    assert interpret(result(**fields), None, 1).outcome == outcome


def test_interpret_without_a_result():
    assert interpret(None, "s", 1, "You've hit your usage limit").outcome == "limit"
    r = interpret(None, "s", 1, "segfault")
    assert (r.outcome, r.error) == ("error", "claude exited 1 without a result: segfault")


FAKE_CLAUDE = """#!{python}
import json, sys
prompt = sys.stdin.read()
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "sess-1"}}))
print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "working"}}]}}}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "num_turns": 2,
                  "structured_output": {{"verdict": "done", "message": "argv=" + " ".join(sys.argv[1:]) + " prompt=" + prompt}}}}))
"""


def test_claude_harness_runs_the_cli(tmp_path):
    binary = tmp_path / "claude"
    binary.write_text(FAKE_CLAUDE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log_path = tmp_path / "logs" / "001-spec.jsonl.gz"

    r = asyncio.run(
        ClaudeHarness(str(binary)).run(
            cwd=tmp_path, prompt="build it", model="sonnet", max_turns=60, log_path=log_path, env={}, resume="old"
        )
    )
    assert (r.outcome, r.session_id) == ("verdict", "sess-1")
    message = r.verdict["message"]
    assert "-p --output-format stream-json --verbose --model sonnet --max-turns 60 --permission-mode bypassPermissions" in message
    assert "--resume old" in message and message.endswith("prompt=build it")
    with gzip.open(log_path, "rt") as f:
        assert [json.loads(line)["type"] for line in f] == ["system", "assistant", "result"]


# --- deployer client ----------------------------------------------------------


def deployer(handler) -> DeployerClient:
    client = httpx.AsyncClient(
        base_url="http://deployer:8000", headers={"X-Deployer-Secret": "s"}, transport=httpx.MockTransport(handler)
    )
    return DeployerClient("http://deployer:8000", "s", "admin", client)


def test_deployer_client_deletes_with_the_admin_secret():
    seen = []

    def handler(request):
        seen.append(request.headers.get("x-deployer-admin-secret"))
        if json.loads(request.content)["slug"] == "never":
            return httpx.Response(422, json={"ok": False, "reasons": ["`never` has not been deployed"]})
        if json.loads(request.content)["slug"] == "off":
            return httpx.Response(403, json={"ok": False, "reasons": ["deleting is off: DEPLOYER_ADMIN_SECRET is not set"]})
        return httpx.Response(200, json={"ok": True, "deleted": "trees"})

    d = deployer(handler)
    asyncio.run(d.delete("trees"))
    asyncio.run(d.delete("never"))  # nothing to delete is fine
    with pytest.raises(DeployerError, match="^deleting is off: DEPLOYER_ADMIN_SECRET is not set$"):
        asyncio.run(d.delete("off"))
    assert seen == ["admin"] * 3


def test_deployer_client_lists_projects():
    d = deployer(lambda request: httpx.Response(200, json={"ok": True, "projects": ["hello", "trees"]}))
    assert asyncio.run(d.projects()) == ["hello", "trees"]
    d = deployer(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(DeployerError, match="^the deployer said 500$"):
        asyncio.run(d.projects())


# --- store --------------------------------------------------------------------


def test_store_inserts_steps_in_order(tmp_path):
    store = Store(tmp_path / "db")
    store.add_project("trees", "Trees", "s")
    job = store.add_job("trees", "new", "idea", "standard")
    store.add_steps(job.id, [Step(0, job.id, 0, kind, kind) for kind in ("setup", "checks", "deploy")])
    store.add_steps(job.id, [Step(0, job.id, 0, "agent", "fix", 150, problem="CI failed"), Step(0, job.id, 0, "push", "push")], after=2)
    steps = store.steps(job.id)
    assert [(s.position, s.name) for s in steps] == [(1, "setup"), (2, "checks"), (3, "fix"), (4, "push"), (5, "deploy")]
    assert (steps[2].max_turns, steps[2].problem, steps[2].state, steps[2].links) == (150, "CI failed", "pending", {})
    assert store.update_step(steps[0].id, links={"repo": "https://x"}).links == {"repo": "https://x"}


def test_store_delete_project_leaves_other_projects_alone(tmp_path):
    store = Store(tmp_path / "db")
    for slug in ("trees", "birds"):
        store.add_project(slug, slug.title(), "s")
        job = store.add_job(slug, "new", "idea", "standard")
        store.add_note(job.id, f"note for {slug}")
        store.start_run(job.id, "spec", "opus/high", "log")
    assert store.delete_project("trees") == [1]
    assert [p.slug for p in store.projects()] == ["birds"]
    assert [j.slug for j in store.jobs()] == ["birds"]
    assert [r["job_id"] for r in store.runs()] == [2]
    assert store.pending_notes(1) == [] and store.pending_notes(2) == [(2, "note for birds")]


# --- golem-deploy ------------------------------------------------------------


def test_deploy_cli_is_scoped_to_the_job_project(monkeypatch):
    calls = []
    monkeypatch.setattr(deploy_cli, "call", lambda method, path, body=None: calls.append((method, path, body)) or 0)
    monkeypatch.setenv("GOLEM_SLUG", "trees")
    assert deploy_cli.main(["status"]) == 0
    assert deploy_cli.main(["logs", "web", "50"]) == 0
    assert deploy_cli.main(["stop"]) == 2
    assert deploy_cli.main(["deploy", "abc"]) == 2  # deploying is golem's own step
    assert calls == [
        ("GET", "/status?slug=trees", None),
        ("GET", "/logs?slug=trees&service=web&tail=50", None),
    ]
    monkeypatch.delenv("GOLEM_SLUG")
    assert deploy_cli.main(["status"]) == 2


def test_structured_output_with_a_custom_schema_is_not_a_verdict():
    """The bug that named a project after the first words of its dictation."""
    brief = {"name": "Leaf Peeper", "slug": "leaf-peeper", "summary": "s"}
    r = interpret(result(structured_output=brief), None, 0, verdict_expected=False)
    assert (r.outcome, r.verdict) == ("verdict", brief)
    assert interpret(result(structured_output=brief), None, 0).outcome == "error"


def test_triage_through_the_claude_harness(tmp_path):
    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\nimport json, sys\nsys.stdin.read()\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'structured_output':"
        " {'name': 'Leaf Peeper', 'slug': 'leaf-peeper', 'summary': 'Guess the tree.', 'mode': 'rigorous',"
        " 'tag': 'Game', 'model': 'unspecified', 'effort': 'max'}}))\n"
    )
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    brief = asyncio.run(
        intake.triage(
            ClaudeHarness(str(binary)), REPO / "prompts" / "intake.md", "hey i would like a game called leaf peeper",
            cwd=tmp_path / "triage", log_path=tmp_path / "triage.jsonl.gz", env={}, tags=["Game"],
        )
    )  # fmt: skip
    assert brief == intake.Brief("Leaf Peeper", "leaf-peeper", "Guess the tree.", tag="Game", mode="rigorous", effort="max")


def test_older_databases_get_new_columns(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "old.db")
    db.execute(
        "CREATE TABLE projects (slug TEXT PRIMARY KEY, name TEXT NOT NULL, summary TEXT NOT NULL,"
        " mode TEXT NOT NULL DEFAULT 'standard', room_id TEXT UNIQUE, paused INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL)"
    )
    db.execute("INSERT INTO projects (slug, name, summary, created_at) VALUES ('hey', 'Hey', 's', 'now')")
    db.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, channel TEXT NOT NULL, who TEXT NOT NULL, text TEXT NOT NULL,"
        " image TEXT, notify INTEGER NOT NULL DEFAULT 0, via TEXT NOT NULL DEFAULT 'old', at TEXT NOT NULL)"
    )
    db.execute("CREATE INDEX messages_by_channel ON messages (channel, id)")
    db.execute("INSERT INTO messages (channel, who, text, at) VALUES ('hey', 'riggy', 'hi', 'now')")
    db.commit()
    db.close()
    db = sqlite3.connect(tmp_path / "old.db")
    db.execute("ALTER TABLE projects ADD COLUMN model TEXT NOT NULL DEFAULT 'opus'")
    db.execute("ALTER TABLE projects ADD COLUMN effort TEXT NOT NULL DEFAULT 'high'")
    db.execute("INSERT INTO projects (slug, name, summary, effort, created_at) VALUES ('max', 'Max', 's', 'max', 'now')")
    db.commit()
    db.close()
    store = Store(tmp_path / "old.db")
    # Projects that named the old defaults now take each step's default; other choices stay.
    assert (store.project("hey").model, store.project("hey").effort) == ("", "")
    assert (store.project("max").model, store.project("max").effort) == ("", "max")
    store.update_project("hey", model="opus")
    assert Store(tmp_path / "old.db").project("hey").model == "opus"  # only once
    # Columns golem no longer uses are ignored, or dropped where they'd get in the way.
    store.add_message("hey", "golem", "hello")
    assert [(m.who, m.text) for m in store.messages("hey")] == [("riggy", "hi"), ("golem", "hello")]
    assert "via" not in {r["name"] for r in store.db.execute("PRAGMA table_info(messages)")}


def test_of_the_tags_cards_had_only_game_stays(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "old.db")
    db.execute(
        "CREATE TABLE projects (slug TEXT PRIMARY KEY, name TEXT NOT NULL, summary TEXT NOT NULL, mode TEXT NOT NULL DEFAULT"
        " 'standard', model TEXT NOT NULL DEFAULT '', effort TEXT NOT NULL DEFAULT '', paused INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE cards (slug TEXT PRIMARY KEY, tagline TEXT NOT NULL, description TEXT NOT NULL, kind TEXT NOT NULL,"
        " tags TEXT NOT NULL DEFAULT '[]', image TEXT, updated_at TEXT NOT NULL)"
    )
    cards = {"trees": ("game", '["Game", "Trees"]'), "quiz": ("app", '["Game", "Quiz"]'), "garden": ("tool", '["Tool", "Gardening"]')}
    for slug, (kind, tags) in {**cards, "birds": ("", "")}.items():
        db.execute("INSERT INTO projects (slug, name, summary, created_at) VALUES (?, ?, 's', 'now')", (slug, slug.title()))
        if kind:
            db.execute("INSERT INTO cards VALUES (?, 't', 'd', ?, ?, NULL, 'now')", (slug, kind, tags))
    db.commit()
    db.close()
    store = Store(tmp_path / "old.db")
    assert {p.slug: p.tag for p in store.projects()} == {"trees": "Game", "quiz": "Game", "garden": "", "birds": ""}
    assert [(t.name, t.color) for t in store.tags()] == [("Game", 7)]
    assert "tags" not in {r["name"] for r in store.db.execute("PRAGMA table_info(cards)")}
    assert store.card("garden").kind == "tool"
    store.delete_tag("Game")
    assert Store(tmp_path / "old.db").tags() == [] and store.project("trees").tag == ""  # once only: what Riggy does stays


def test_tags_move_with_their_project(tmp_path):
    store = Store(tmp_path / "golem.db")
    store.add_project("trees", "Trees", "s")
    store.update_project("trees", tag="Game")
    assert store.move_project("trees", "tree-cards").tag == "Game"
    assert store.tag_counts() == {"Game": 1}


def test_a_job_reads_as_the_pieces_of_work_riggy_would_name():
    from golem import progress

    def step(position, kind, name="", state="pending", **more):
        return Step(position, 1, position, kind, name or kind, state=state, **more)

    steps = [
        step(1, "setup", state="done"), step(2, "agent", "spec", state="done"), step(3, "agent", "scaffold", state="done"),
        step(4, "push", state="done"), step(5, "pr", state="done"), step(6, "checks", state="failed"),
        step(7, "agent", "fix", state="done", problem="CI failed: lint"), step(8, "push", state="done"),
        step(9, "checks", state="done"), step(10, "merge", state="done"), step(11, "deploy", state="running", started_at="now"),
        step(12, "agent", "mvp"), step(13, "checkin"), step(14, "alert"),
    ]  # fmt: skip
    found = progress.pieces(steps)
    # Setting up (done) and the alert aren't work Riggy asked for; the shipping steps belong to the work they ship.
    assert [(p.name, p.state) for p in found] == [
        ("Plan it", "done"), ("Set up hosting", "done"), ("Fix what broke", "now"), ("Build the MVP", "todo"), ("You try it", "todo")
    ]  # fmt: skip
    assert found[2].shipping.kind == "deploy" and not found[2].live and not found[1].live  # its CI failed: the fix ships it
    steps[10].state = "done"
    found = progress.pieces(steps)
    assert found[2].live and found[2].state == "done" and progress.current(found).name == "Build the MVP"
    steps[0].state = "running"
    assert progress.pieces(steps)[0].name == "Get ready"  # shown while it runs


def test_the_most_specific_model_and_effort_win(golem):
    from golem.plan import engine

    store = golem.store
    project = store.add_project("trees", "Trees", "s")
    docs = Step(1, 1, 1, "agent", "docs")
    assert str(engine(golem.settings, store, project, docs)) == "opus/high"  # GOLEM_MODEL, GOLEM_EFFORT
    store.set_default("*", "effort", "medium")
    store.set_default("docs", "model", "haiku")
    e = engine(golem.settings, store, project, docs)
    assert (str(e), e.model_from, e.effort_from) == ("haiku/medium", "the docs default", "the default")
    assert str(engine(golem.settings, store, project, Step(2, 1, 2, "agent", "spec"))) == "opus/medium"
    project = store.update_project("trees", effort="max")
    assert str(engine(golem.settings, store, project, docs)) == "haiku/max"
    docs.model, docs.effort = "sonnet", "low"
    e = engine(golem.settings, store, project, docs)
    assert (str(e), e.model_from, e.effort_from) == ("sonnet/low", "this step", "this step")


def test_suggested_defaults_fill_gaps_once(golem):
    from golem.control import Control
    from golem.plan import SUGGESTED, SUGGESTED_KEY

    assert set(SUGGESTED) == set(Control(golem.store, golem.scheduler).kinds())  # one for every kind of step
    assert all(m in MODELS and e in EFFORTS for m, e in SUGGESTED.values())
    store = golem.store
    store.set_default("docs", "model", "haiku")  # Riggy's own choice stays
    store.suggest_defaults(SUGGESTED_KEY, SUGGESTED)
    assert store.defaults()["docs"] == ("haiku", "medium")
    assert store.defaults()["spec"] == ("opus", "xhigh")
    store.set_default("spec", "effort", "")  # unset by Riggy afterwards: stays unset
    store.suggest_defaults(SUGGESTED_KEY, SUGGESTED)
    assert store.defaults()["spec"] == ("opus", "")
    store.db.execute("DELETE FROM defaults WHERE name = 'examine'")  # a kind of step new since
    store.suggest_defaults(SUGGESTED_KEY, SUGGESTED)
    assert store.defaults()["examine"] == SUGGESTED["examine"] and store.defaults()["spec"] == ("opus", "")


# --- golem's own git steps, on a real repo ---------------------------------------


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A GitHubWorkspace whose `trees` checkout has a local bare repo as origin."""
    import subprocess

    from golem.workspace import GitHubWorkspace

    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "golem")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "golem@example.com")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    (tmp_path / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")
    subprocess.run(["git", "init", "--quiet", "--bare", str(tmp_path / "origin.git")], check=True)
    subprocess.run(["git", "clone", "--quiet", str(tmp_path / "origin.git"), str(tmp_path / "ws" / "trees")], check=True)
    return GitHubWorkspace(tmp_path / "ws", "unlaunched")


def git(ws, *args) -> str:
    import subprocess

    return subprocess.run(["git", *args], cwd=ws.path("trees"), capture_output=True, text=True, check=True).stdout.strip()


def test_a_question_leaves_the_checkout_as_it_found_it(checkout):
    ws = checkout

    async def go():
        await ws.ensure_main("trees", "# Trees\n")
        (ws.path("trees") / "node_modules").mkdir()
        (ws.path("trees") / "node_modules" / "dep.js").write_text("x\n")
        assert await ws.clean("trees") and await ws.current_branch("trees") == "main"  # dependencies don't count
        (ws.path("trees") / "README.md").write_text("changed\n")
        (ws.path("trees") / "scratch.txt").write_text("notes\n")
        assert not await ws.clean("trees")
        await ws.discard("trees")
        assert await ws.clean("trees") and (ws.path("trees") / "README.md").read_text() == "# Trees\n"
        assert not (ws.path("trees") / "scratch.txt").exists() and (ws.path("trees") / "node_modules" / "dep.js").exists()

    asyncio.run(go())


def test_git_steps_branch_commit_push_and_land(checkout):
    ws = checkout

    async def go():
        await ws.ensure_main("trees", "# Trees\n")
        assert git(ws, "log", "--oneline", "origin/main").endswith("Start the project")
        await ws.ensure_main("trees", "# Trees\n")  # once only
        assert await ws.push_branch("trees", "golem/job-1") is None  # nothing new for main

        await ws.start_branch("trees", "golem/job-1")
        assert git(ws, "branch", "--show-current") == "golem/job-1"
        (ws.path("trees") / "app.js").write_text("hi\n")
        (ws.path("trees") / "node_modules").mkdir()
        (ws.path("trees") / "node_modules" / "dep.js").write_text("x\n")
        assert await ws.commit_all("trees", "Work left uncommitted")
        assert git(ws, "show", "--name-only", "--format=", "HEAD") == "app.js"  # never node_modules
        assert not await ws.commit_all("trees", "again")

        sha = await ws.push_branch("trees", "golem/job-1")
        assert sha == git(ws, "rev-parse", "origin/golem/job-1")
        await ws.start_branch("trees", "golem/job-1")  # already on it: stays

        assert await ws.push_main("trees", "golem/job-1") == sha  # reckless: straight to main
        assert git(ws, "branch", "--show-current") == "main" and await ws.main_sha("trees") == sha
        assert "golem/job-1" not in git(ws, "branch")  # gone locally, ready for the next work

    asyncio.run(go())


def test_ci_is_read_like_the_deployer_reads_it(checkout, monkeypatch):
    from golem import workspace

    runs = {"workflow_runs": [
        {"id": 1, "workflow_id": 9, "name": "build", "status": "completed", "conclusion": "failure", "html_url": "u1"},
        {"id": 2, "workflow_id": 9, "name": "build", "status": "in_progress", "conclusion": None, "html_url": "u2"},
    ]}  # fmt: skip

    async def fake_run(*args, cwd=None):
        if args[:2] == ("gh", "api"):
            return 0, json.dumps(runs)
        return 0, "npm test: 2 failing"

    monkeypatch.setattr(workspace, "run", fake_run)
    ci = asyncio.run(checkout.ci("trees", "abc"))
    assert (ci.state, ci.detail, ci.url) == ("pending", "build: in_progress", "u2")  # the rerun replaces the failure
    runs["workflow_runs"][1].update(status="completed", conclusion="failure")
    ci = asyncio.run(checkout.ci("trees", "abc"))
    assert (ci.state, ci.detail) == ("failed", "build: failure\n\nnpm test: 2 failing")
    runs["workflow_runs"][1].update(conclusion="success")
    assert asyncio.run(checkout.ci("trees", "abc")).state == "passed"
    runs["workflow_runs"].clear()
    assert asyncio.run(checkout.ci("trees", "abc")).state == "none"


def test_deployer_client_deploys_and_says_what_to_fix():
    def handler(request):
        sha = json.loads(request.content)["sha"]
        if sha == "good":
            return httpx.Response(200, json={"ok": True, "url": "https://trees.unlaunched.dev", "sha": sha})
        if sha == "rules":
            return httpx.Response(422, json={"ok": False, "reasons": ["ports: not allowed", "mem_limit missing"]})
        if sha == "crash":
            return httpx.Response(502, json={"ok": False, "error": "didn't answer", "logs": "Error: EADDRINUSE"})
        return httpx.Response(502, json={"ok": False, "error": "GitHub returned 401"})

    d = deployer(handler)
    assert asyncio.run(d.deploy("trees", "good")).ok
    assert asyncio.run(d.deploy("trees", "rules")).problem == "ports: not allowed\nmem_limit missing"
    assert asyncio.run(d.deploy("trees", "crash")).problem == "didn't answer\n\nLogs:\nError: EADDRINUSE"
    with pytest.raises(DeployerError, match="GitHub returned 401"):  # golem's setup, not the project's code
        asyncio.run(d.deploy("trees", "config"))


def test_a_token_without_actions_access_says_how_to_fix_it(checkout, monkeypatch):
    from golem import workspace

    async def forbidden(*args, cwd=None):
        return 1, '{"message":"Resource not accessible by personal access token","status":"403"}'

    monkeypatch.setattr(workspace, "run", forbidden)
    with pytest.raises(workspace.WorkspaceError, match="give it Actions read-only"):
        asyncio.run(checkout.ci("trees", "abc"))
