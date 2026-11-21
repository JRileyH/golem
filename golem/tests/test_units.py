"""Smaller pieces: playbooks, intake, the harness's reading of Claude Code output, the deploy CLI."""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import signal
import stat
import sys
import time

import httpx
import pytest
from conftest import REPO

from golem import config, deploy_cli, intake, options, plan
from golem import playbook as playbooks
from golem.config import Settings
from golem.deployer_client import DeployerClient, DeployerError
from golem.harness import ClaudeHarness, interpret
from golem.store import EFFORTS, MODELS, Step, Store
from golem.workspace import About

# --- settings -----------------------------------------------------------------


def test_settings_need_a_domain_and_an_org():
    for env in ({"GITHUB_ORG": "me"}, {"BASE_DOMAIN": "example.com"}):
        with pytest.raises(SystemExit, match="BASE_DOMAIN and GITHUB_ORG must be set"):
            Settings.from_env(env)


def test_settings_need_a_web_password():
    for password in ({}, {"GOLEM_WEB_PASSWORD": ""}):
        with pytest.raises(SystemExit, match="GOLEM_WEB_PASSWORD must be set"):
            Settings.from_env({"BASE_DOMAIN": "example.com", "GITHUB_ORG": "me", **password})


def test_the_memory_cap_is_read_from_the_container(tmp_path):
    v2, v1 = tmp_path / "memory.max", tmp_path / "memory.limit_in_bytes"
    assert config.memory_cap((v2, v1)) is None  # not in a container
    v1.write_text(f"{1536 * 2**20}\n")
    assert config.memory_cap((v2, v1)) == 1536 * 2**20  # cgroup v1
    v1.write_text("9223372036854771712\n")
    assert config.memory_cap((v2, v1)) is None  # v1's way of saying none
    v2.write_text("max\n")
    assert config.memory_cap((v2, v1)) is None
    v2.write_text("1073741824\n")
    assert config.memory_cap((v2, v1)) == 2**30  # v2 first


@pytest.mark.parametrize(
    "domain, edge_port, url",
    [
        ("example.com", "", "https://golem.example.com"),
        ("example.com", "127.0.0.1:8800", "https://golem.example.com"),  # behind the box's own proxy
        ("localhost", "8800", "http://golem.localhost:8800"),
        ("localhost", "127.0.0.1:8800", "http://golem.localhost:8800"),
        ("localhost", "80", "http://golem.localhost"),
    ],
)
def test_links_use_traefiks_port_only_locally(domain, edge_port, url):
    env = {"BASE_DOMAIN": domain, "GITHUB_ORG": "me", "GOLEM_WEB_PASSWORD": "pw", "EDGE_PORT": edge_port}
    assert Settings.from_env(env).url("golem") == url


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
        ("standard", "standard", ["spec", "scaffold", "mvp", "design", "guide", "apply", "buildout", "review", "docs"]),
        ("rigorous", "rigorous", ["spec", "spec-review", "scaffold", "mvp", "design", "guide", "apply", "buildout", "review", "second-pass", "docs"]),
        ("feature", "reckless", ["change", "docs"]),
        ("feature", "standard", ["change", "review", "docs"]),
        ("feature", "rigorous", ["change", "review", "second-pass", "docs"]),
        ("options", "reckless", ["options", "change", "docs"]),
        ("options", "standard", ["options", "change", "review", "docs"]),
        ("design", "reckless", ["design", "guide", "apply", "docs"]),
        ("design", "standard", ["design", "guide", "apply", "review", "docs"]),
        ("design", "rigorous", ["design", "guide", "apply", "review", "second-pass", "docs"]),
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


def test_the_adopt_playbook_checks_first_and_ships_in_every_mode():
    book = playbooks.load(REPO / "playbooks" / "adopt.md")
    for mode in ("reckless", "standard", "rigorous"):
        assert [s.name for s in book.for_mode(mode)] == ["vet", "setup", "adopt", "push", "pr", "checks", "merge", "deploy", "alert"]
    assert "What golem's checks found" in book.intro and "skills/new-repo.md" in book.instructions("adopt")


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
        ("[setup, vet, {agent: a, turns: 5}, deploy, alert]", "## a\nx", "vet can only be the first step"),
        ("[vet, refresh, {agent: a, turns: 5}, answer]", "## a\nx", "or start with vet, setup and end with deploy, alert"),
        ("[vet, {agent: a, turns: 5}, deploy, alert]", "## a\nx", "must start with setup"),
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


MIMIC = About("mimic", "Tabletop data in, cards out", False, True, "# mimic\n\nTabletop data in, visual outputs out. $words stays.\n")


def adopt_triage(tmp_path, script, words="cards for my D&D table, use sonnet"):
    from golem.harness import FakeHarness

    harness = FakeHarness(script)
    brief = asyncio.run(intake.adopt_triage(
        harness, REPO / "prompts" / "adopt.md", "mimic", words, MIMIC, tmp_path, tmp_path / "log.gz", {}, ["Game"]
    ))  # fmt: skip
    return brief, harness.calls


def test_an_adopted_repos_brief_puts_his_words_first(tmp_path):
    verdict = {"name": "Mimic", "summary": "Printable cards for a D&D table.", "tag": "none", "mode": "unspecified",
               "model": "sonnet", "effort": "unspecified"}  # fmt: skip
    brief, [call] = adopt_triage(tmp_path, [verdict])
    assert brief == intake.Brief("Mimic", "mimic", "Printable cards for a D&D table.", model="sonnet")
    assert (call.model, call.label) == ("haiku", "adopt")
    assert "slug" not in call.schema["properties"] and "slug" not in call.schema["required"]
    assert "cards for my D&D table, use sonnet" in call.prompt and "Tabletop data in, cards out" in call.prompt
    assert "outputs out. $words stays." in call.prompt  # filled in one pass: the README's own text stays as it is


def test_an_adopted_repos_brief_without_a_model(tmp_path):
    from golem.harness import RunResult

    brief, _ = adopt_triage(tmp_path, [RunResult("error", error="boom")], words="")
    assert brief == intake.Brief("mimic", "mimic", "Tabletop data in, cards out")  # the README's heading, the description
    brief, _ = adopt_triage(tmp_path, [RunResult("error", error="boom")], words="my card printer")
    assert brief.summary == "my card printer"  # his words win
    bare = intake.adopt_fallback("card-shop", "", About("card-shop", "", False, True, "Prints cards.\n"))
    assert (bare.name, bare.summary) == ("Card Shop", "Prints cards.")


# --- job titles ----------------------------------------------------------------


def test_a_models_title_is_tidied():
    assert plan.clean_title(" “Library  View\nRedesign.” ") == "Library View Redesign"
    assert plan.clean_title(None) == plan.clean_title("  ") == ""
    long = plan.clean_title("Word " * 30)
    assert len(long) == plan.TITLE_CHARS and long.endswith("…")


def test_a_job_without_a_title_goes_by_its_request(tmp_path):
    store = Store(tmp_path / "db")
    store.add_project("mimic", "Mimic", "s")
    titled = store.add_job("mimic", "change", "I'd like you to make it a grid", "feature", title="Grid Layout")
    adopted = store.add_job("mimic", "adopt", "!adopt mimic cards for my table", "adopt")
    rambling = store.add_job("mimic", "change", "so basically " * 10 + "\nand more", "feature")
    assert plan.title(titled) == "Grid Layout"
    assert plan.title(adopted) == "cards for my table"  # not the command
    assert len(plan.title(rambling)) == plan.TITLE_CHARS and plan.title(rambling).endswith("…")


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


def test_collect_design_keeps_a_doc_that_runs_nothing_and_its_shared_pages(tmp_path):
    checkout, dest = tmp_path / "repo", tmp_path / "state"
    rounds = checkout / "docs/design/rounds/job-7/round-1"
    rounds.mkdir(parents=True)
    (rounds / "doc.html").write_text("<!doctype html><style>body{}</style><section class=page>Directions</section>")
    for n in range(1, 6):
        (rounds / f"p{n}.png").write_bytes(PNG)
    raw = {
        "doc": "docs/design/rounds/job-7/round-1/doc.html",
        "pages": ["../secret.png", *(f"docs/design/rounds/job-7/round-1/p{n}.png" for n in range(1, 6))],
        "questions": ["Wordmark:   Oxanium or Zilla Slab?", "", "Icon: Granite or Kiln?"],
        "recommendation": "B, with Oxanium and the Kiln icon.",
    }
    design, problem = options.collect_design(raw, checkout, dest, "12")
    assert problem == "" and (design.doc, design.source) == ("12-doc.html", "docs/design/rounds/job-7/round-1/doc.html")
    assert design.pages == ["12-p-1.png", "12-p-2.png", "12-p-3.png", "12-p-4.png"]  # the first four
    assert design.questions == ["Wordmark: Oxanium or Zilla Slab?", "Icon: Granite or Kiln?"]
    assert (dest / "12-doc.html").read_text().startswith("<!doctype html>")
    assert options.is_doc("12-doc.html") and options.is_image("12-p-1.png")
    assert not any(options.is_doc(name) for name in ("12-doc.htm", "../12-doc.html", "x-doc.html", "12-doc.html.png"))

    def problem_with(doc: str, text: str | bytes = "<p>hi</p>") -> str:
        path = checkout / doc
        path.write_bytes(text) if isinstance(text, bytes) else path.write_text(text)
        return options.collect_design({"doc": doc}, checkout, tmp_path / "other", "13")[1]

    assert "has a <script>" in problem_with("a.html", "<p>hi</p><SCRIPT>fetch('/act')</SCRIPT>")
    assert "has a <script>" in problem_with("b.html", "<meta http-equiv=refresh content='0;url=/act'>")
    assert "isn't an HTML file" in problem_with("c.pdf")
    assert "is over 10 MB" in problem_with("d.html", b"x" * (options.MAX_DOC_BYTES + 1))
    assert "returned no doc" in options.collect_design({"doc": "../outside.html"}, checkout, dest, "14")[1]
    assert not (tmp_path / "other").exists()  # nothing copied for a doc it refused
    skipped, problem = options.collect_design({"skip": "He said keep the look."}, checkout, dest, "15")
    assert (skipped.skip, skipped.doc, problem) == ("He said keep the look.", "", "")


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


@pytest.mark.parametrize(
    "text, kind",
    [
        ("give it a design pass", "design"),
        ("redesign it so it feels like a field guide", "design"),
        ("it needs a new look and a logo", "design"),
        ("show me some logo options and I'll pick", "options"),
        ("make the button blue", "change"),
        ("why is the header grey?", "question"),
    ],
)
def test_what_a_message_asks_for_without_a_model(golem, text, kind):
    from dataclasses import replace

    fake = replace(golem.settings, harness="fake")
    assert asyncio.run(options.read_request(golem.harness, fake, text)) == options.Request(kind, "")


def test_what_a_pick_tells_the_next_step():
    found = [options.Option("A", "Round", "", ["docs/o/a/1.png", "docs/o/a/x.css"], []), options.Option("B", "Ink", "", ["docs/o/b.png"], [])]
    assert options.picked(options.Reply(False, ["B"]), found, "b please") == "You picked B (Ink). You said: “b please”. Files: docs/o/b.png."
    assert options.picked(options.Reply(False, ["A", "B"]), found, "") == (
        "You picked A (Round) and B (Ink). Files: A in docs/o/a/; B in docs/o/b.png."
    )
    assert options.picked(options.Reply(False, []), found, "you choose") == (
        "You didn't pick one option, so the next step decides from what you said. You said: “you choose”."
    )


def test_what_another_round_tells_the_next_step():
    found = [options.Option("A", "Field guide", "", ["docs/r/a/1.png"], []), options.Option("B", "Arcade", "", [], [])]
    assert options.again(options.Reply(True, ["A"]), found, "a but more icons", True) == (
        "You settled on A (Field guide) (docs/r/a/1.png) and asked for another round: “a but more icons”"
    )
    assert options.again(options.Reply(True, []), found, "warmer", True) == "You asked for another round: “warmer”"
    assert options.again(options.Reply(True, []), found, "none of these", False) == "You asked for other options: “none of these”"
    assert options.plain_reply("another round please", ["A", "B"]) == options.Reply(True, [])  # without a model
    assert options.plain_reply("different directions", ["A", "B"]).more


def test_a_pick_is_about_the_round_just_before_it():
    one = [{"letter": x, "title": x, "description": "", "files": [], "images": []} for x in "AB"]
    first = Step(1, 1, 2, "agent", "options", options=one)
    second = Step(3, 1, 4, "agent", "options", state="skipped")  # another round, skipped from the status page
    picks = [Step(2, 1, 3, "choose", "choose"), Step(4, 1, 5, "choose", "choose")]
    steps = [first, picks[0], second, picks[1]]
    assert options.shown(steps, picks[0]) == (first, [options.Option(**o) for o in one])
    assert options.shown(steps, picks[1]) == (second, [])  # not round one's again
    assert options.shown(steps, first)[0] is first and options.shown(steps, Step(5, 1, 6, "agent", "change")) == (None, [])


def test_the_doc_renderer_renders_in_the_sandbox_golem_serves_docs_in():
    from golem.web import DOC_POLICY

    tool = (REPO / "golem/tools/golem-render-doc.mjs").read_text()
    assert f'const POLICY = "{DOC_POLICY}";' in tool  # what a worker sees is what Riggy sees
    assert f"MAX_BYTES = {options.MAX_IMAGE_BYTES // 2**20} * 1024 * 1024" in tool


def test_every_worker_tool_is_installed():
    dockerfile = (REPO / "golem/Dockerfile").read_text()
    for tool in sorted((REPO / "golem/tools").glob("golem-*.mjs")):
        assert f"golem/tools/{tool.name} /usr/local/bin/{tool.stem}" in dockerfile, tool.name


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


LEAVES_SOMETHING_RUNNING = """#!{python}
import json, subprocess, sys
sys.stdin.read()
left = subprocess.Popen(["sleep", "300"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                  "structured_output": {{"verdict": "done", "message": str(left.pid)}}}}))
"""


def test_what_a_run_leaves_running_is_stopped_when_it_ends(tmp_path):
    binary = tmp_path / "claude"
    binary.write_text(LEAVES_SOMETHING_RUNNING.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    r = asyncio.run(
        ClaudeHarness(str(binary)).run(
            cwd=tmp_path, prompt="serve it", model="sonnet", max_turns=5, log_path=tmp_path / "log.jsonl.gz",
            env={"PATH": os.environ["PATH"], "GOLEM_TMP": str(tmp_path)},
        )  # fmt: skip
    )
    left = int(r.verdict["message"])
    for _ in range(200):  # a dev server it forgot would hold its port for the next worker
        try:
            os.kill(left, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    os.kill(left, signal.SIGKILL)
    raise AssertionError("the process the run left behind is still running")


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


def test_deployer_client_validates_a_commit():
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        if json.loads(request.content)["slug"] == "bad":
            return httpx.Response(422, json={"ok": False, "reasons": ["service `web`: `ports` is not allowed", "a memory limit is required"]})
        if json.loads(request.content)["slug"] == "down":
            return httpx.Response(502, json={"ok": False, "error": "GitHub returned 401 reading deploy/compose.yml"})
        return httpx.Response(200, json={"ok": True, "services": ["web"], "public_service": "web", "port": 80})

    d = deployer(handler)
    assert asyncio.run(d.validate("mimic", "a" * 40)).ok
    found = asyncio.run(d.validate("bad", "a" * 40))
    assert (found.ok, found.problem) == (False, "service `web`: `ports` is not allowed\na memory limit is required")
    with pytest.raises(DeployerError, match="^GitHub returned 401"):  # not the project's fault
        asyncio.run(d.validate("down", "a" * 40))
    assert seen[0] == ("/validate", {"slug": "mimic", "sha": "a" * 40})


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


def test_notes_aimed_at_one_step_are_for_it_alone(tmp_path):
    store = Store(tmp_path / "db")
    store.add_project("trees", "Trees", "s")
    job = store.add_job("trees", "new", "idea", "standard")
    store.add_steps(job.id, [Step(0, job.id, 0, "agent", name) for name in ("design", "buildout")])
    design, buildout = store.steps(job.id)
    store.add_note(job.id, "the score is broken")
    store.aim_notes([i for i, _ in store.pending_notes(job.id)], buildout.id)
    assert store.pending_notes(job.id) == [] and store.pending_notes(job.id, design.id) == []
    assert [text for _, text in store.pending_notes(job.id, buildout.id)] == ["the score is broken"]


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
        " {'name': 'Leaf Peeper', 'slug': 'leaf-peeper', 'summary': 'Guess the tree.', 'title': 'Tree Guessing Game', 'mode': 'rigorous',"
        " 'tag': 'Game', 'model': 'unspecified', 'effort': 'max'}}))\n"
    )
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    brief = asyncio.run(
        intake.triage(
            ClaudeHarness(str(binary)), REPO / "prompts" / "intake.md", "hey i would like a game called leaf peeper",
            cwd=tmp_path / "triage", log_path=tmp_path / "triage.jsonl.gz", env={}, tags=["Game"],
        )
    )  # fmt: skip
    assert brief == intake.Brief("Leaf Peeper", "leaf-peeper", "Guess the tree.", tag="Game", mode="rigorous", effort="max", title="Tree Guessing Game")


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
    db.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY, slug TEXT NOT NULL, kind TEXT NOT NULL, request TEXT NOT NULL,"
        " playbook TEXT NOT NULL, phase INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'queued',"
        " priority INTEGER NOT NULL DEFAULT 0, resume_session TEXT, attempts INTEGER NOT NULL DEFAULT 0, retry_at TEXT,"
        " issue INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    db.execute("INSERT INTO jobs (slug, kind, request, playbook, created_at, updated_at) VALUES ('hey', 'change', 'add a quiz', 'feature', 'now', 'now')")
    db.execute(
        "CREATE TABLE steps (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, position INTEGER NOT NULL, kind TEXT NOT NULL,"
        " name TEXT NOT NULL, max_turns INTEGER, state TEXT NOT NULL DEFAULT 'pending', summary TEXT, problem TEXT, error TEXT,"
        " links TEXT NOT NULL DEFAULT '{}', started_at TEXT, ended_at TEXT)"
    )
    db.execute("INSERT INTO steps (job_id, position, kind, name) VALUES (1, 1, 'agent', 'change')")
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
    # Jobs from before titles have none: they go by their request's first line.
    assert (store.job(1).title, plan.title(store.job(1))) == ("", "add a quiz")
    # Steps from before options and design rounds have none.
    [step] = store.steps(1)
    assert (step.options, step.design, step.optional) == ([], {}, False)
    store.update_step(step.id, design={"doc": "1-doc.html"})
    assert store.step(step.id).design == {"doc": "1-doc.html"}


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


def in_tree(path, *args) -> str:
    import subprocess

    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout.strip()


def test_each_job_works_in_a_tree_of_its_own(checkout):
    ws = checkout

    async def go():
        await ws.ensure_main("trees", "# Trees\n")
        one = await ws.ensure_tree("trees", 1)
        assert one == ws.root / ".jobs" / "1" and in_tree(one, "rev-parse", "HEAD") == git(ws, "rev-parse", "origin/main")
        assert await ws.current_branch("trees", job=1) == "HEAD"  # detached until its first step
        assert git(ws, "branch", "--show-current") == "main"  # the project's checkout stays on main
        assert await ws.ensure_tree("trees", 1) == one  # there already: as it is

        await ws.start_branch("trees", "golem/job-1", job=1)
        (one / "app.js").write_text("hi\n")
        assert await ws.commit_all("trees", "Work", job=1)
        assert await ws.current_branch("trees", job=1) == "golem/job-1" and git(ws, "branch", "--show-current") == "main"

        # A question about job 1's work looks in a tree of its own, and puts back only that.
        two = await ws.ensure_tree("trees", 2, at="golem/job-1")
        assert (two / "app.js").read_text() == "hi\n" and await ws.current_branch("trees", job=2) == "HEAD"
        (two / "app.js").write_text("changed\n")
        (two / "scratch.txt").write_text("notes\n")
        await ws.discard("trees", job=2)
        assert (two / "app.js").read_text() == "hi\n" and not (two / "scratch.txt").exists()
        assert (one / "app.js").read_text() == "hi\n" and await ws.clean("trees", job=1)

        sha = await ws.push_branch("trees", "golem/job-1", job=1)
        assert sha == git(ws, "rev-parse", "origin/golem/job-1")
        assert await ws.push_main("trees", "golem/job-1", job=1) == sha
        assert await ws.current_branch("trees", job=1) == "HEAD" and in_tree(one, "rev-parse", "HEAD") == sha  # landed on main
        assert "golem/job-1" not in git(ws, "branch") and git(ws, "branch", "--show-current") == "main"

        # A tree that's lost comes back on its job's branch, with its work.
        three = await ws.ensure_tree("trees", 3)
        await ws.start_branch("trees", "golem/job-3", job=3)
        (three / "more.js").write_text("more\n")
        await ws.commit_all("trees", "More", job=3)
        await workspace.remove_tree(three)
        assert await ws.ensure_tree("trees", 3) == three
        assert await ws.current_branch("trees", job=3) == "golem/job-3" and (three / "more.js").read_text() == "more\n"

        # A question about a job that has no branch yet looks at main.
        four = await ws.ensure_tree("trees", 4, at="golem/job-99")
        assert in_tree(four, "rev-parse", "HEAD") == git(ws, "rev-parse", "origin/main")

        await ws.prune_trees({3})  # only job 3 is still under way
        assert sorted(p.name for p in (ws.root / ".jobs").iterdir()) == ["3"]
        assert len(git(ws, "worktree", "list").splitlines()) == 2  # the checkout and job 3's tree
        await ws.drop_tree("trees", 3)
        assert not three.exists() and len(git(ws, "worktree", "list").splitlines()) == 1

    from golem import workspace

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


def test_about_describes_a_repo_golem_did_not_make(checkout, monkeypatch):
    from golem import workspace
    from golem.workspace import About, LocalWorkspace

    calls = []

    async def fake_run(*args, cwd=None):
        calls.append(args)
        if args[:3] == ("gh", "repo", "view"):
            if args[3] == "unlaunched/gone":
                return 1, "GraphQL: Could not resolve to a Repository"
            return 0, json.dumps({"name": "mimic", "description": "Tabletop cards", "isArchived": False})
        if args[-1].endswith("branches/main"):
            return 1, '{"message":"Branch not found"}'
        return 0, "# mimic\n\nTabletop data in."

    monkeypatch.setattr(workspace, "run", fake_run)
    assert asyncio.run(checkout.about("mimic")) == About("mimic", "Tabletop cards", False, False, "# mimic\n\nTabletop data in.")
    assert ("gh", "api", "--silent", "repos/unlaunched/mimic/branches/main") in calls
    assert asyncio.run(checkout.about("gone")) is None

    # The pretend GitHub: a directory already in its workspace is a repo golem didn't make.
    local = LocalWorkspace(checkout.root)
    (checkout.root / "mimic").mkdir()
    (checkout.root / "mimic" / "README.md").write_text("# mimic\n")
    assert asyncio.run(local.about("mimic")) == About("mimic", "", False, True, "# mimic\n")
    assert asyncio.run(local.repo_exists("mimic")) and asyncio.run(local.about("nothing")) is None


def test_leftover_prs_on_golems_job_branches_are_closed(checkout, monkeypatch):
    from golem import workspace

    calls = []

    async def fake_run(*args, cwd=None):
        calls.append(args)
        if args[:3] == ("gh", "pr", "list"):
            return 0, json.dumps([
                {"number": 3, "headRefName": "golem/job-3"}, {"number": 5, "headRefName": "riggy/tinkering"},
                {"number": 9, "headRefName": "golem/job-12"},
            ])  # fmt: skip
        return 0, ""

    monkeypatch.setattr(workspace, "run", fake_run)
    assert asyncio.run(checkout.close_leftover_prs("mimic")) == [3, 9]  # golem's own branches only
    closes = [c for c in calls if c[:3] == ("gh", "pr", "close")]
    assert [c[3] for c in closes] == ["3", "9"] and all(c[-1] == workspace.LEFTOVER for c in closes)


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
