"""The adoption checklist (adopt.py): each check on its own, on files and fakes."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from golem import adopt
from golem.adopt import BLOCKING, FIXABLE, PASS, Check
from golem.deployer_client import Deployment
from golem.workspace import CI, About

REPO = Path(__file__).resolve().parents[2]
URL = "https://mimic.unlaunched.dev/"
ARM = "linux/arm64"


def skill_workflow(platform: str = ARM) -> str:
    """The build workflow skills/new-repo.md tells workers to write."""
    text = (REPO / "skills" / "new-repo.md").read_text()
    return re.search(r"## The build workflow.*?```yaml\n(.*?)```", text, re.S)[1].replace("<$GOLEM_PLATFORM>", platform)


def golem_shaped(path: Path) -> Path:
    """A checkout with everything a golem project has."""
    files = {
        "Dockerfile": "FROM python:3.13-slim\n",
        ".github/workflows/build.yml": skill_workflow(),
        "deploy/compose.yml": "services:\n  web:\n    image: ghcr.io/unlaunched/mimic\n    mem_limit: 256m\n"
        "    labels:\n      golem.public: \"true\"\n      golem.port: \"8765\"\n",
        "AGENTS.md": "# mimic\n",
        "CLAUDE.md": "See AGENTS.md.\n",
        "README.md": f"# mimic\n\nLive at {URL}\n\nTabletop data in, cards out.\n",
        "docs/brief.md": "# mimic\n",
        "docs/specs/current.md": "# What it does\n",
        "docs/decisions.md": "- Adopted.\n",
        "docs/lessons.md": "",
        "tests/test_app.py": "def test_ok(): pass\n",
        "bin/test": "#!/bin/sh\nset -e\nuv run pytest\n",
    }
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    (path / "bin" / "test").chmod(0o755)
    return path


def checks(path: Path, platform: str = ARM) -> dict[str, Check]:
    return {c.name: c for c in adopt.files(path, "unlaunched", "mimic", platform, URL)}


# --- preflight -----------------------------------------------------------------


def about(**overrides) -> About:
    return About(**{"name": "mimic", "description": "Cards", "archived": False, "has_main": True, "readme": "# mimic", **overrides})


def test_preflight_passes_a_repo_golem_can_take_on():
    assert adopt.preflight("mimic", about(), False, "unlaunched") == []


@pytest.mark.parametrize(
    "slug, found, is_project, said",
    [
        ("golem", about(name="golem"), False, "golem is one of golem's own addresses. Rename the repo"),
        ("my_app", about(name="my_app"), False, "my_app won't work as an address"),
        ("mimic", about(), True, "mimic is already one of my projects."),
        ("mimic", None, False, "I can't see github.com/unlaunched/mimic. Is it in unlaunched?"),
        ("mimic", about(name="Mimic"), False, "The repo is called Mimic, and addresses and image names are lowercase."),
        ("mimic", about(archived=True), False, "It's archived"),
        ("mimic", about(has_main=False), False, "It has no main branch"),
    ],
)
def test_preflight_blocks_with_what_to_change(slug, found, is_project, said):
    [check] = adopt.preflight(slug, found, is_project, "unlaunched")
    assert check.state == BLOCKING and check.said.startswith(said)


def test_preflight_says_every_github_problem_at_once():
    found = adopt.preflight("mimic", about(name="Mimic", archived=True, has_main=False), False, "unlaunched")
    assert [c.name for c in found] == ["name", "archived", "main branch"]


# --- the checkout --------------------------------------------------------------


def test_a_golem_shaped_repo_passes_every_file_check(tmp_path):
    found = checks(golem_shaped(tmp_path))
    assert {name: c.state for name, c in found.items()} == {
        "Dockerfile": PASS, "build workflow": PASS, "repo shape": PASS, "docs/brief.md": PASS,
    }  # fmt: skip
    assert found["build workflow"].said == "build.yml"


def test_an_empty_repo_is_all_fixable(tmp_path):
    found = checks(tmp_path)
    assert {c.state for c in found.values()} == {FIXABLE}
    assert found["build workflow"].said == "There's no GitHub Actions workflow to test it, then build and push its image."
    assert found["repo shape"].said == (
        "Missing AGENTS.md, a CLAUDE.md pointing to AGENTS.md, a README that starts with the live URL "
        f"({URL}), an executable bin/test that runs its tests, docs/specs/, docs/decisions.md and docs/lessons.md."
    )
    assert found["docs/brief.md"].said == "I'll write it from what you said and the README."


def test_repo_shape_wants_the_live_url_first_and_a_spec(tmp_path):
    golem_shaped(tmp_path)
    (tmp_path / "README.md").write_text("# mimic\n\n" + "A line.\n" * 6 + f"Live at {URL}\n")
    (tmp_path / "docs" / "specs" / "current.md").unlink()
    (tmp_path / "CLAUDE.md").unlink()
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")  # a link to it counts
    (tmp_path / "bin" / "test").chmod(0o644)  # there, but it won't run
    assert checks(tmp_path)["repo shape"].said == (
        f"Missing a README that starts with the live URL ({URL}), an executable bin/test that runs its tests and docs/specs/."
    )


def test_hellos_workflow_passes_with_nothing_to_test(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "build.yml").write_text((REPO / "examples/hello/.github/workflows/build.yml").read_text())
    assert adopt.workflow(tmp_path, "unlaunched", "hello", ARM).state == PASS


@pytest.mark.parametrize(
    "change, problem",
    [
        (lambda t: t.replace("  pull_request:\n", ""), "doesn't run on pull requests"),
        (lambda t: t.replace("branches: [main]", "branches: [develop]"), "doesn't run on pushes to main"),
        (lambda t: t.replace("${{ github.sha }}", "latest"), "doesn't tag the image ghcr.io/unlaunched/mimic:<commit sha>"),
        (lambda t: t.replace(f"platforms: {ARM}", "platforms: linux/amd64"), f"doesn't build it for the server ({ARM})"),
        (lambda t: t.replace(f"          platforms: {ARM}\n", ""), f"doesn't build it for the server ({ARM})"),
        (lambda t: t.replace("push: ${{ github.event_name == 'push' }}", "push: false"), "doesn't push the image"),
        (lambda t: t.replace("    needs: test\n", ""), "builds the image without waiting for the tests"),
    ],
)
def test_a_workflow_unlike_the_skills_is_fixable_and_says_how(tmp_path, change, problem):
    golem_shaped(tmp_path)
    workflow = tmp_path / ".github" / "workflows" / "build.yml"
    workflow.write_text(change(workflow.read_text()))
    check = adopt.workflow(tmp_path, "unlaunched", "mimic", ARM)
    assert check.state == FIXABLE and check.said.startswith(f"build.yml {problem}")


def test_workflows_written_other_ways_still_pass(tmp_path):
    golem_shaped(tmp_path)
    workflow = tmp_path / ".github" / "workflows" / "build.yml"
    text = workflow.read_text().replace("on:\n  push:\n    branches: [main]\n  pull_request:\n", "on: [push, pull_request]\n")
    text = text.replace("ghcr.io/${{ github.repository }}:${{ github.sha }}", "ghcr.io/unlaunched/mimic:${{github.sha}}")
    workflow.write_text(text)
    assert adopt.workflow(tmp_path, "unlaunched", "mimic", ARM).state == PASS
    # On x86 a workflow that names no platform builds for the server anyway.
    workflow.write_text(skill_workflow().replace(f"          platforms: {ARM}\n", ""))
    assert adopt.workflow(tmp_path, "unlaunched", "mimic", "linux/amd64").state == PASS


def test_a_workflow_that_builds_no_image_doesnt_count(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "lint.yml").write_text("on: push\njobs:\n  lint:\n    steps:\n      - run: ruff check\n")
    (tmp_path / ".github" / "workflows" / "broken.yml").write_text("on: [push\n")
    check = adopt.workflow(tmp_path, "unlaunched", "mimic", ARM)
    assert (check.state, check.said) == (FIXABLE, "No workflow builds the image (with docker/build-push-action).")


def test_has_tests(tmp_path):
    assert not adopt.has_tests(tmp_path)
    (tmp_path / "package.json").write_text('{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}')
    assert not adopt.has_tests(tmp_path)
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    assert adopt.has_tests(tmp_path)


# --- CI, the deployer and the survey ---------------------------------------------


def test_ci_on_main():
    assert adopt.ci(CI("passed", "build: success")) == Check("CI on main", PASS, "build: success")
    assert adopt.ci(CI("failed", "build: failure\n\nlog")).said == "CI fails on main's latest commit (build: failure)."
    assert adopt.ci(CI("none")) == Check("CI on main", FIXABLE, "No CI ran on main's latest commit.")


def test_compose_is_judged_by_the_deployer():
    assert adopt.compose(True, Deployment(True)).state == PASS
    refused = adopt.compose(True, Deployment(False, problem="service `mimic`: `ports` is not allowed\na memory limit is required"))
    assert refused == Check("deploy/compose.yml", FIXABLE, "The deployer refuses it: service `mimic`: `ports` is not allowed; a memory limit is required.")
    assert adopt.compose(False, None).said.startswith("There's no deploy/compose.yml")


def ran(result: str, detail: str = "559 passed") -> dict:
    return {"result": result, "command": "uv run pytest", "detail": detail}


def test_survey_passes_what_builds_tests_and_needs_nothing():
    assert adopt.survey({"tests": ran("passed"), "missing": [], "needs": []}) == [
        Check("tests in golem's container", PASS, "uv run pytest: 559 passed")
    ]
    assert adopt.survey({"tests": ran("none", ""), "missing": [], "needs": []})[0].said == "found none to run"


def test_survey_failing_tests_are_a_workers_to_fix():
    [check] = adopt.survey({"tests": ran("failed", "test_pdf: page count 3 != 4"), "missing": [], "needs": []})
    assert (check.state, check.said) == (FIXABLE, "They fail: test_pdf: page count 3 != 4")


def test_survey_blocks_on_what_golem_cant_give():
    found = adopt.survey({
        "tests": ran("failed", "25 failed: cannot load libpangoft2"),
        "missing": [{"what": "libpangoft2-1.0-0", "for": "WeasyPrint"}],
        "needs": [{"what": "a command run by hand in its container", "why": "API keys come from `mimic key`", "change": "make a key on first start"}],
    })  # fmt: skip
    assert [(c.name, c.state) for c in found] == [("golem's container", BLOCKING), ("needs", BLOCKING)]
    assert found[0].said.startswith("Building or testing it needs libpangoft2-1.0-0 (for WeasyPrint), which golem's container")
    assert found[0].said.endswith("or change the project so it doesn't need it.")
    assert found[1].said == "It needs a command run by hand in its container: API keys come from `mimic key` To change that: make a key on first start"


# --- what golem does with them ---------------------------------------------------


def test_decide_and_what_the_worker_gets():
    passed, brief, gap = Check("Dockerfile", PASS), Check("docs/brief.md", FIXABLE, "I'll write it."), Check("CI on main", FIXABLE, "It fails.")
    assert adopt.decide([passed]) == "adopt"
    assert adopt.decide([passed, brief]) == "fix" and not adopt.needs_worker([passed, brief])  # golem writes the brief itself
    assert adopt.decide([brief, gap]) == "fix" and adopt.needs_worker([brief, gap])
    assert adopt.decide([gap, Check("needs", BLOCKING, "A secret.")]) == "refuse"
    assert adopt.task([passed, brief, gap]) == "- CI on main: It fails."
    assert adopt.report([passed, gap, Check("needs", BLOCKING, "A secret.")]) == "✓ Dockerfile\n→ CI on main: It fails.\n✗ needs: A secret."


def test_refusal_says_what_to_change_and_what_golem_would_fix():
    text = adopt.refusal("mimic", [Check("needs", BLOCKING, "It needs a Stripe key."), Check("repo shape", FIXABLE, "Missing AGENTS.md.")])
    assert text == (
        "I didn't adopt mimic. What's in the way:\n- It needs a Stripe key.\n\n"
        "The rest I'd have fixed myself: repo shape.\n\nOnce that's changed, adopt it again."
    )


def test_the_brief_says_where_the_project_came_from():
    text = adopt.brief("Mimic", "Printable cards for D&D.", "cards for my table\nkeep it simple", "https://github.com/unlaunched/mimic", "2026-11-20")
    assert text.startswith("# Mimic\n\nPrintable cards for D&D.\n\n## Where it came from\n\nAdopted by golem on 2026-11-20")
    assert text.endswith("Riggy said:\n\n> cards for my table\n> keep it simple\n")
    assert "comes from its README" in adopt.brief("Mimic", "s", "", "r", "d")
