"""Demo data, for looking at the web app: a few projects with jobs in every state (waiting on
Riggy, running, queued, failed and fixing, done), walked there the way the scheduler walks
them, without running anything. For a local golem only:

    uv run --env-file <local settings> python -m golem.demo

Never on a real golem: it writes made-up projects into its database.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from . import plan, proposals
from .config import Settings
from .harness import FakeHarness
from .prompts import Prompts
from .scheduler import Outcome, Scheduler
from .store import Store
from .talk import STATUS, Talk
from .workspace import LocalWorkspace


def main() -> None:
    settings = Settings.from_env()
    if settings.harness != "fake" or settings.base_domain != "localhost":
        sys.exit("golem.demo is for a local golem with HARNESS=fake only.")
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.state_dir / "golem.db")
    talk = Talk(store)
    scheduler = Scheduler(settings, store, talk, FakeHarness(), LocalWorkspace(settings.workspace_dir), Prompts(settings), None)
    scheduler.library.seed()
    walk = Walker(store, scheduler)

    trees = walk.project("tree-cards", "Tree Cards", "A flashcard game for northeastern US trees.", tag="Game")
    job = walk.job(trees, "new", "web_app", "", "a flashcard game for identifying northeastern us trees", "Tree Flashcard Game")
    walk.to(job, "your check-in")
    walk.wait(job, "Try the quiz: ten trees a round, photos of bark and leaves.\nhttp://tree-cards.localhost/\n\n"
                   "Try it and reply here: say it looks good, or what to change.")  # fmt: skip

    leaf = walk.project("leaf-peeper", "Leaf Peeper", "Guess the tree from one leaf, against the clock.", tag="Game")
    job = walk.job(leaf, "new", "web_app", "", "make a game called Leaf Peeper", "Leaf Guessing Game")
    walk.to(job, "mvp")
    walk.run(job, "The board and pieces render on a phone; starting on scoring.")

    birds = walk.project("birds", "Birdsong", "Learn birds by their songs.")
    old = walk.job(birds, "new", "web_app", "", "learn birds by their songs", "Bird Song Trainer")
    walk.finish(old)
    walk.job(birds, "change", "change", "", "add a streak counter", "Streak Counter")
    walk.job(birds, "look", "question", "", "why is the first song so quiet?", "Why the First Song Is Quiet")

    tips = walk.project("tip-calc", "Tip Calc", "Split a bill and the tip, fast.")
    old = walk.job(tips, "new", "web_app", "", "a tiny tip calculator", "Tip Calculator")
    walk.finish(old)
    job = walk.job(tips, "change", "change", "", "round the tip to the nearest dollar", "Round the Tip")
    walk.to(job, "CI")
    for detail in ("npm test: 2 failing", "npm test: 1 failing"):
        walk.fail(job, detail)
        walk.to(job, "CI")
    walk.run(job, "")

    recipes = walk.project("recipes", "Recipe Scaler", "Scale a recipe to any number of servings.")
    old = walk.job(recipes, "new", "web_app", "", "scale recipes", "Recipe Scaler")
    walk.finish(old)
    done = walk.job(recipes, "change", "change", "", "show grams and cups side by side", "Grams and Cups")
    walk.finish(done)
    looked = walk.job(recipes, "propose", "proposals", "", "Look at its features for things to propose", "")
    walk.finish(looked)
    store.add_proposals("recipes", looked.id, "features", [proposals.collect([demo])[0] for demo in (
        {"title": "Remember each recipe's units", "gain": "Cooks stop re-picking cups on every visit.",
         "changes": ["The unit choice sticks per recipe", "A new recipe starts in your last unit"],
         "saw": "The toggle resets on reload: src/Scaler.tsx keeps it in memory only.", "tradeoff": "", "size": "small",
         "plan": "Keep the unit per recipe id in localStorage (src/Scaler.tsx), read it on load.\nCheck: switch to cups, reload, still cups."},
        {"title": "Share a scaled recipe by link", "gain": "Send the 12-serving version to a friend in one tap.",
         "changes": ["A Share button on each recipe", "The link opens it already scaled"],
         "saw": "Servings live only in component state; the URL never changes.", "tradeoff": "Links break if a recipe is renamed.",
         "size": "medium", "plan": "Put servings and unit in the query string (?s=12&u=g); read them on load; Share uses navigator.share."},
    )])  # fmt: skip
    moss = walk.project("moss-map", "Moss Map", "Log the mosses on your walks, on a map.")
    old = walk.job(moss, "new", "web_app", "", "a map of mosses I find on walks", "Moss Map")
    walk.finish(old)
    job = walk.job(moss, "change", "options", "", "a new look for the map pins", "Map Pin Styles")
    walk.to(job, "options")
    made = walk.current(job)
    pins = [("A", "Leaf pins", "A small leaf for each find, tinted by moss type."), ("B", "Dots and rings", "A dot, with a ring when you've found it more than once."),
            ("C", "Hand-drawn", "Pencil-sketch pins, like a field notebook.")]  # fmt: skip
    store.update_step(made.id, options=[{"letter": k, "title": t, "description": d, "files": [f"src/pins/{k.lower()}.svg"], "images": []} for k, t, d in pins])
    walk.to(job, "your pick")
    walk.wait(job, "Three styles for the map pins. Pick one, or say what you'd change.")

    store.add_message(STATUS, "golem", "Upgraded golem to 4af06fb: workflows.")
    print("Demo projects: tree-cards, leaf-peeper, birds, tip-calc, recipes, moss-map.")


class Walker:
    """Walks jobs to where the demo wants them, a step at a time, as the scheduler would."""

    def __init__(self, store: Store, scheduler: Scheduler):
        self.store, self.scheduler = store, scheduler
        self.at = datetime.now(UTC) - timedelta(hours=3)

    def tick(self) -> str:
        self.at += timedelta(minutes=4)
        return self.at.isoformat(timespec="seconds")

    def project(self, slug: str, name: str, summary: str, tag: str = ""):
        if self.store.project(slug):
            sys.exit(f"{slug} is there already: golem.demo is for an empty golem.")
        project = self.store.add_project(slug, name, summary)
        if tag:
            if self.store.tag(tag) is None:
                self.store.add_tag(tag)
            project = self.store.update_project(slug, tag=tag)
        return project

    def job(self, project, start: str, workflow: str, mode: str, request: str, title: str):
        kind = {"new": "new"}.get(start, "change")
        job = self.store.add_job(project.slug, kind, request, workflow, title=title, start=start)
        job = self.store.update_job(job.id, workflow=workflow, mode=mode)
        self.scheduler.ensure_started(job)
        return self.store.job(job.id)

    def current(self, job):
        return plan.current(self.store.steps(job.id))

    def close(self, job, outcome: Outcome) -> None:
        s = self.current(job)
        started, ended = self.tick(), self.tick()
        closed = {"state": outcome.state, "summary": outcome.summary, "started_at": started, "ended_at": ended,
                  "error": outcome.problem if outcome.state == "failed" else None, "links": outcome.links}  # fmt: skip
        self.scheduler.walk_on(self.store.job(job.id), s, outcome, closed)
        if outcome.state == "done" and s.kind == "agent":
            self.store.add_message(job.slug, "golem", f"✓ {plan.label(s)}: {outcome.summary}", job_id=job.id, step_id=s.id)

    def to(self, job, label: str) -> None:
        """Every step done, as it comes, up to the one labelled `label`."""
        for _ in range(300):
            s = self.current(job)
            if s is None or plan.label(s) == label:
                return
            self.close(job, Outcome("done", SAID.get(s.name, f"{plan.label(s).capitalize()} done.")))

    def fail(self, job, detail: str) -> None:
        self.close(job, Outcome("failed", "CI failed on PR #1", problem=f"CI failed on PR #1:\n{detail}", port="fail"))
        self.store.add_message(job.slug, "golem", "✗ CI: CI failed on PR #1. Fixing it.", job_id=job.id)

    def run(self, job, note: str) -> None:
        s = self.current(job)
        self.store.update_step(s.id, state="running", started_at=self.tick())
        self.store.update_job(job.id, state="running")
        if note:
            self.store.add_message(job.slug, "golem", f"▸ {note}", job_id=job.id, step_id=s.id)

    def wait(self, job, asked: str) -> None:
        s = self.current(job)
        self.store.update_step(s.id, state="waiting", started_at=self.tick(), summary="Waiting for your reply.")
        self.store.update_job(job.id, state="waiting")
        self.store.add_message(job.slug, "golem", asked, notify=True, job_id=job.id, step_id=s.id)

    def finish(self, job) -> None:
        self.to(job, "\0")
        self.store.update_job(job.id, state="done")


SAID = {
    "spec": "MVP: pick the tree from four photos.", "scaffold": "A styled placeholder page, deployed.",
    "mvp": "The quiz works: ten trees a round.", "build": "Built the whole thing in one pass.",
    "change": "Made the change, with tests.", "review": "Fixed a scoring bug on skip.", "docs": "Docs and lessons up to date.",
    "examine": "It's quiet because the first clip is normalised lower; I'd raise it.", "propose": "Looked at its features.",
    "fix": "Fixed the failing test.", "setup": "Repo, checkout and issue ready.", "push": "Pushed.", "pr": "PR #1 is open.",
    "checks": "CI passed.", "merge": "Merged.", "deploy": "Live.", "guide": "Wrote the design guide.", "apply": "Restyled it.",
    "options": "Three styles for the map pins.",
}  # fmt: skip


if __name__ == "__main__":
    main()
