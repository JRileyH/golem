"""Adopting a repo golem didn't make: the checklist it runs before taking one on.

Adopting (the New project page, bot.py) takes a repo in the org and from then on treats it like a project
golem built. First golem checks it over, and each check passes, is fixable or is blocking:

- fixable: a worker can change it in the repo. The adopt job's `adopt` step does, following
  the skills, then golem pushes, merges and deploys as usual. docs/brief.md golem writes itself.
- blocking: it's outside the repo, something only Riggy can give or change: a secret, a GitHub
  setting, a package in golem's own image. golem doesn't adopt it, and says what would change it.

The bot's quick checks need only GitHub (`preflight`). The `vet` step's need the checkout
(`files`), CI on main (`ci`), the deployer's own rules (`compose`), and the survey: a worker
run that builds and tests the repo in golem's container, changes nothing, and says what the app
needs that golem can't give (`survey`, prompts/survey.md).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .deployer_client import Deployment
from .intake import RESERVED, valid_slug
from .workspace import CI, About

PASS, FIXABLE, BLOCKING = "pass", "fixable", "blocking"
MARKS = {PASS: "✓", FIXABLE: "→", BLOCKING: "✗"}
BRIEF = "docs/brief.md"  # the one gap golem fills itself, without a worker
SHA_TAG = ":${{github.sha}}"  # how CI tags the image (expressions written without spaces)
RUNNER_PLATFORM = "linux/amd64"  # what an image is built for when the workflow names no platform


@dataclass(frozen=True)
class Check:
    name: str  # what was checked: "build workflow"
    state: str  # pass | fixable | blocking
    said: str = ""  # plain words: what's wrong and what would change it; for a pass, a note or ""


# --- before anything is taken on ----------------------------------------------


def preflight(slug: str, about: About | None, is_project: bool, org: str) -> list[Check]:
    """The quick checks, from GitHub alone: those that failed, all blocking."""
    if not valid_slug(slug):
        why = f"{slug} is one of golem's own addresses" if slug in RESERVED else (
            f"{slug} won't work as an address (2 to 31 lowercase letters, digits and hyphens)"
        )
        return [Check("name", BLOCKING, f"{why}. Rename the repo on GitHub (Settings, General) and adopt it by its new name.")]
    if is_project:
        return [Check("project", BLOCKING, f"{slug} is already one of my projects.")]
    if about is None:
        return [Check("repo", BLOCKING, (
            f"I can't see github.com/{org}/{slug}. Is it in {org}? If golem's GitHub token only reaches "
            "selected repos, add this one to it (GitHub: Settings, Developer settings, the token)."
        ))]  # fmt: skip
    found = []
    if about.name != slug:
        found.append(Check("name", BLOCKING, (
            f"The repo is called {about.name}, and addresses and image names are lowercase. "
            f"Rename it to {slug} on GitHub (Settings, General)."
        )))  # fmt: skip
    if about.archived:
        found.append(Check("archived", BLOCKING, "It's archived, so nothing can be pushed to it. Unarchive it on GitHub (Settings, at the bottom)."))
    if not about.has_main:
        found.append(Check("main branch", BLOCKING, (
            "It has no main branch, which golem merges to and deploys from. "
            "Rename its default branch to main on GitHub (Settings, General, Default branch)."
        )))  # fmt: skip
    return found


# --- the checkout -------------------------------------------------------------


def files(path: Path, org: str, slug: str, platform: str, url: str) -> list[Check]:
    """How much of every golem project's shape the checkout has (skills/new-repo.md)."""
    found = [
        Check("Dockerfile", PASS) if (path / "Dockerfile").is_file()
        else Check("Dockerfile", FIXABLE, "There's no Dockerfile at the top of the repo for CI to build the image from."),
        workflow(path, org, slug, platform),
    ]  # fmt: skip
    missing = []
    if not (path / "AGENTS.md").is_file():
        missing.append("AGENTS.md")
    if not _points_to_agents(path / "CLAUDE.md"):
        missing.append("a CLAUDE.md pointing to AGENTS.md")
    if not _leads_with(path / "README.md", url):
        missing.append(f"a README that starts with the live URL ({url})")
    if not (path / "bin" / "test").is_file() or not os.access(path / "bin" / "test", os.X_OK):
        missing.append("an executable bin/test that runs its tests")
    specs = path / "docs" / "specs"
    if not (specs.is_dir() and any(p.is_file() for p in specs.iterdir())):
        missing.append("docs/specs/")
    missing += [name for name in ("docs/decisions.md", "docs/lessons.md") if not (path / name).is_file()]
    found.append(Check("repo shape", FIXABLE, f"Missing {_and(missing)}.") if missing else Check("repo shape", PASS))
    if (path / BRIEF).is_file():
        found.append(Check(BRIEF, PASS))
    else:
        found.append(Check(BRIEF, FIXABLE, "I'll write it from what you said and the README."))
    return found


def workflow(path: Path, org: str, slug: str, platform: str) -> Check:
    """Whether a GitHub Actions workflow runs the tests, then builds the image for the server and
    pushes it tagged with the commit, on pull requests and pushes to main (skills/new-repo.md)."""
    name = "build workflow"
    found = sorted(p for p in (path / ".github" / "workflows").glob("*.y*ml") if p.is_file())
    if not found:
        return Check(name, FIXABLE, "There's no GitHub Actions workflow to test it, then build and push its image.")
    best: tuple[str, list[str]] | None = None
    for file in found:
        problems = workflow_problems(file.read_text(errors="replace"), org, slug, platform, has_tests(path))
        if problems == []:
            return Check(name, PASS, file.name)
        if problems and (best is None or len(problems) < len(best[1])):
            best = (file.name, problems)
    if best is None:
        return Check(name, FIXABLE, "No workflow builds the image (with docker/build-push-action).")
    return Check(name, FIXABLE, f"{best[0]} {_and(best[1])}.")


def workflow_problems(text: str, org: str, slug: str, platform: str, tested: bool) -> list[str] | None:
    """What a workflow does differently from the skill's, or None if it doesn't build an image."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return None
    builds = [
        (job, step)
        for job in jobs.values() if isinstance(job, dict)
        for step in job.get("steps") or [] if isinstance(step, dict) and str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]  # fmt: skip
    if not builds:
        return None
    job, step = builds[0]
    opts = step.get("with") if isinstance(step.get("with"), dict) else {}
    on = doc.get("on", doc.get(True))  # YAML 1.1 reads a bare `on:` key as true
    problems = []
    if not _triggered(on, "pull_request"):
        problems.append("doesn't run on pull requests")
    if not _pushes_to_main(on):
        problems.append("doesn't run on pushes to main")
    if not _words(opts.get("tags")) & {"ghcr.io/${{github.repository}}" + SHA_TAG, f"ghcr.io/{org}/{slug}" + SHA_TAG}:
        problems.append(f"doesn't tag the image ghcr.io/{org}/{slug}:<commit sha>")
    platforms = _words(opts.get("platforms"))
    if platform not in platforms and (platforms or platform != RUNNER_PLATFORM):
        problems.append(f"doesn't build it for the server ({platform})")
    push = opts.get("push")
    if not (push is True or push == "true" or (isinstance(push, str) and "${{" in push)):
        problems.append("doesn't push the image")
    if tested and not job.get("needs"):
        problems.append("builds the image without waiting for the tests (run them as their own job, which the image job needs)")
    return problems


def has_tests(path: Path) -> bool:
    """Whether the repo looks like it has tests: a tests folder, or an npm test script."""
    if (path / "tests").is_dir() or (path / "test").is_dir():
        return True
    try:
        script = json.loads((path / "package.json").read_text()).get("scripts", {}).get("test", "")
    except (OSError, ValueError, AttributeError):
        return False
    return bool(script) and "no test specified" not in script


# --- CI, the deployer and the survey ------------------------------------------


def ci(result: CI) -> Check:
    """CI on main's latest commit, once it's finished (a pending run is waited for)."""
    if result.state == "passed":
        return Check("CI on main", PASS, result.detail)
    if result.state == "failed":
        return Check("CI on main", FIXABLE, f"CI fails on main's latest commit ({result.detail.splitlines()[0] if result.detail else 'failed'}).")
    return Check("CI on main", FIXABLE, "No CI ran on main's latest commit.")


def compose(present: bool, verdict: Deployment | None) -> Check:
    """deploy/compose.yml, judged by the deployer's own rules (its /validate) when there is one."""
    name = "deploy/compose.yml"
    if not present:
        return Check(name, FIXABLE, "There's no deploy/compose.yml for the deployer to run (skills/deploy.md).")
    if verdict is None or verdict.ok:
        return Check(name, PASS)
    reasons = [r.strip() for r in verdict.problem.splitlines() if r.strip()]
    return Check(name, FIXABLE, f"The deployer refuses it: {'; '.join(reasons)}.")


_ITEM = {"type": "object", "additionalProperties": False}
SURVEY_SCHEMA = {
    "type": "object",
    "properties": {
        "tests": {
            **_ITEM,
            "properties": {
                "result": {"enum": ["passed", "failed", "none"], "description": "none: there are no tests to run"},
                "command": {"type": "string", "description": "What you ran"},
                "detail": {"type": "string", "description": "How many passed; if any failed, which and why, briefly"},
            },
            "required": ["result", "command", "detail"],
        },
        "missing": {
            "type": "array",
            "description": "System packages or tools building or testing it needs that this container lacks and you can't install",
            "items": {**_ITEM, "properties": {"what": {"type": "string"}, "for": {"type": "string"}}, "required": ["what", "for"]},
        },
        "needs": {
            "type": "array",
            "description": "Things it needs in order to run that golem can't give it",
            "items": {
                **_ITEM,
                "properties": {
                    "what": {"type": "string"},
                    "why": {"type": "string", "description": "Where in the code or docs, briefly"},
                    "change": {"type": "string", "description": "What would remove the need"},
                },
                "required": ["what", "why", "change"],
            },
        },
    },
    "required": ["tests", "missing", "needs"],
    "additionalProperties": False,
}


def survey(report: dict) -> list[Check]:
    """The survey's findings as checks. Missing packages and needs golem can't meet block; tests
    that fail for the code's own reasons are a worker's to fix."""
    tests = report.get("tests") if isinstance(report.get("tests"), dict) else {}
    missing = [m for m in report.get("missing") or [] if isinstance(m, dict) and str(m.get("what", "")).strip()]
    needs = [n for n in report.get("needs") or [] if isinstance(n, dict) and str(n.get("what", "")).strip()]
    name = "tests in golem's container"
    found = []
    if missing:
        listed = "; ".join(f"{m['what']} (for {m.get('for') or 'its build'})" for m in missing)
        found.append(Check("golem's container", BLOCKING, (
            f"Building or testing it needs {listed}, which golem's container doesn't have and workers can't install. "
            "Either add it to golem/Dockerfile and upgrade golem, or change the project so it doesn't need it."
        )))  # fmt: skip
    elif tests.get("result") == "failed":
        found.append(Check(name, FIXABLE, f"They fail: {_clip(tests.get('detail') or 'no detail')}"))
    elif tests.get("result") == "passed":
        ran = f"{tests.get('command', '').strip()}: " if tests.get("command") else ""
        found.append(Check(name, PASS, _clip(ran + str(tests.get("detail") or "passed"))))
    else:
        found.append(Check(name, PASS, "found none to run"))
    for n in needs:
        change = str(n.get("change") or "").strip()
        found.append(Check("needs", BLOCKING, f"It needs {n['what']}: {_clip(n.get('why') or '')}" + (f" To change that: {change}" if change else "")))
    return found


# --- what golem does with them -------------------------------------------------


def decide(checks: list[Check]) -> str:
    """refuse (something blocks), fix (something's fixable) or adopt (all passed)."""
    states = {c.state for c in checks}
    return "refuse" if BLOCKING in states else "fix" if FIXABLE in states else "adopt"


def needs_worker(checks: list[Check]) -> bool:
    """Whether the adopt step has anything to do: every fixable gap but the brief is a worker's."""
    return any(c.state == FIXABLE and c.name != BRIEF for c in checks)


def report(checks: list[Check]) -> str:
    """The checklist, a line each, for Riggy's phone."""
    return "\n".join(f"{MARKS[c.state]} {c.name}" + (f": {c.said}" if c.said else "") for c in checks)


def task(checks: list[Check]) -> str:
    """What the adopt step has to fix, for its prompt."""
    return "\n".join(f"- {c.name}: {c.said}" for c in checks if c.state == FIXABLE and c.name != BRIEF)


def refusal(slug: str, checks: list[Check]) -> str:
    """Why golem won't take the repo on, and what would change that."""
    blocking = [c.said for c in checks if c.state == BLOCKING]
    fixable = [c.name for c in checks if c.state == FIXABLE]
    text = f"I didn't adopt {slug}. What's in the way:\n" + "\n".join(f"- {said}" for said in blocking)
    if fixable:
        text += f"\n\nThe rest I'd have fixed myself: {_and(fixable)}."
    return text + "\n\nOnce that's changed, adopt it again."


def brief(name: str, summary: str, said: str, repo: str, day: str) -> str:
    """docs/brief.md for an adopted repo without one: the brief, and where the project came from."""
    quoted = "\n".join(f"> {line}".rstrip() for line in said.strip().splitlines())
    words = f"Riggy said:\n\n{quoted}" if quoted else "Riggy said nothing more, so the brief comes from its README and description."
    return f"# {name}\n\n{summary}\n\n## Where it came from\n\nAdopted by golem on {day} from an existing repo, {repo}. {words}\n"


# --- helpers ------------------------------------------------------------------


def _triggered(on: object, event: str) -> bool:
    if isinstance(on, str):
        return on == event
    return isinstance(on, (list, dict)) and event in on


def _pushes_to_main(on: object) -> bool:
    if not _triggered(on, "push"):
        return False
    spec = on.get("push") if isinstance(on, dict) else None
    if not isinstance(spec, dict):
        return True  # every branch
    branches = spec.get("branches")
    if branches is None:
        return "main" not in (spec.get("branches-ignore") or [])
    branches = [branches] if isinstance(branches, str) else branches
    return any(b in ("main", "*", "**") for b in branches)


def _words(value: object) -> set[str]:
    """A workflow value (a string or a list) split into words, with `${{ x }}` written `${{x}}`."""
    text = " ".join(map(str, value)) if isinstance(value, list) else str(value or "")
    text = re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda m: "${{" + m[1] + "}}", text)
    return {w for w in re.split(r"[\s,]+", text) if w}


def _points_to_agents(claude: Path) -> bool:
    if claude.is_symlink():
        return claude.resolve().name == "AGENTS.md"
    try:
        return "AGENTS.md" in claude.read_text(errors="replace")
    except OSError:
        return False


def _leads_with(readme: Path, url: str) -> bool:
    """Whether the live URL is in the README's first few lines."""
    try:
        lines = [line for line in readme.read_text(errors="replace").splitlines() if line.strip()][:5]
    except OSError:
        return False
    return any(url.rstrip("/") in line for line in lines)


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _clip(text: object, limit: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
