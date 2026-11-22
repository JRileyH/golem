"""Turn the templates in prompts/ into the text a worker gets."""

from __future__ import annotations

from pathlib import Path
from string import Template

from . import plan
from .config import Settings
from .proposals import LENSES, Lens, kinds, past
from .store import Job, Project, Proposal, Step

# Golem's own steps worth telling a worker about in "Done so far".
TOLD = {"merge", "push-main", "deploy", "checkin", "choose"}


class Prompts:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir = settings.process_dir / "prompts"

    def render(self, template: str, **values: object) -> str:
        text = (self.dir / f"{template}.md").read_text()
        return Template(text).safe_substitute({k: str(v) for k, v in values.items()})

    def step(
        self,
        project: Project,
        job: Job,
        intro: str,
        instructions: str,
        step: Step,
        steps: list[Step],
        branch: str,
        feedback: list[str],
        lessons: str,
        outputs: list[dict] = (),
        after: str = "",
    ) -> str:
        """An agent step: its block's instructions (with what the blocks before found, Scheduler.instructions)
        in golem's frame. A block with several outputs ends with the one it takes instead of done."""
        s = self.settings
        if outputs:
            ways = "\n".join(f"- `{o['name']}`: {o.get('about') or ''}".rstrip(": ") for o in outputs)
            instructions += (
                "\n\n## How this step ends\n\nInstead of `done`, your verdict is the one of these that fits "
                f"(or `blocked`, as below). It decides what golem does next.\n\n{ways}"
            )
        earlier = ""
        if step.error:
            earlier = (
                "# An earlier attempt\n\nThis step was started before and didn't finish: "
                f"{step.error}\nWhatever it committed is on the branch; carry on from there."
            )
        return self.render(
            "step",
            name=project.name,
            slug=project.slug,
            summary=project.summary,
            mode=job.mode or "standard",
            url=s.project_url(project.slug),
            repo=s.repo_url(project.slug),
            branch=branch,
            kind={"new": "new project", "adopt": "adopting a repo golem didn't make"}.get(job.kind, "change to an existing project"),
            request=job.request,
            issue=f"#{job.issue} ({s.repo_url(project.slug)}/issues/{job.issue})" if job.issue else "none",
            intro=intro,
            done=format_done(steps),
            step=step.name,
            after=after or "Golem decides what comes after your step by the job's workflow.",
            instructions=instructions,
            earlier=earlier,
            feedback=format_feedback(feedback),
            lessons=lessons.strip() or "(none yet)",
            lessons_path=s.state_dir / "lessons.md",
            proposals_dir=s.state_dir / "proposals",
            job=job.id,
            skills=s.process_dir / "skills",
        )

    def question(self, project: Project, job: Job, intro: str, instructions: str, step: Step, branch: str, busy: bool,
                 jobs: list[str], said: list[str], feedback: list[str]) -> str:  # fmt: skip
        """A question's step: what the project is, what's happened lately, and what he asked."""
        s = self.settings
        more = "\n\n".join(feedback)
        return self.render(
            "question",
            name=project.name,
            slug=project.slug,
            summary=project.summary,
            url=s.project_url(project.slug),
            repo=s.repo_url(project.slug),
            branch=branch,
            where=" (a job is under way: this is its work so far, not yet live)" if busy else " (what's live)",
            request=job.request,
            feedback=f"He added:\n\n{format_feedback(feedback)}" if more else "",
            jobs="\n".join(jobs) or "(none yet)",
            said="\n".join(said) or "(nothing yet)",
            step=step.name,
            intro=intro,
            instructions=instructions,
            skills=s.process_dir / "skills",
        )

    def proposals(self, project: Project, job: Job, intro: str, instructions: str, step: Step, branch: str, lens: Lens,
                  jobs: list[str], said: list[str], earlier: list[Proposal]) -> str:  # fmt: skip
        """A proposals job's step: the project, what's happened lately, the lens to look through,
        and every earlier proposal with what Riggy made of it."""
        s = self.settings
        return self.render(
            "proposals",
            name=project.name,
            slug=project.slug,
            summary=project.summary,
            mode=job.mode or "standard",
            url=s.project_url(project.slug),
            repo=s.repo_url(project.slug),
            branch=branch,
            lens=lens.name,
            focus=lens.focus,
            lenses=", ".join(x.name for x in LENSES if x in kinds(project) or x == lens),
            jobs="\n".join(jobs) or "(none yet)",
            said="\n".join(said) or "(nothing yet)",
            past=past(earlier),
            step=step.name,
            intro=intro,
            instructions=instructions,
            skills=s.process_dir / "skills",
        )

    def survey(self, project: Project, branch: str, said: str) -> str:
        """The survey an adoption's check runs (adopt.py): build and test it here, and say what it
        needs that golem can't give. `said`: Riggy's words after `!adopt <slug>`."""
        s = self.settings
        return self.render(
            "survey",
            name=project.name,
            slug=project.slug,
            summary=project.summary,
            url=s.project_url(project.slug),
            repo=s.repo_url(project.slug),
            branch=branch,
            platform=s.platform,
            said=format_feedback([said]) if said.strip() else "(nothing more)",
        )

    def resume(self, feedback: list[str]) -> str:
        return self.render("resume", feedback=format_feedback(feedback))

    def wrapup(self, makes: bool = False) -> str:
        """`makes`: the step returns what it made with its verdict (options, proposals…)."""
        more = (
            "If it's finished, also give what the step said to return with its verdict (its "
            "options, or whatever else the step asked for), exactly as you would have.\n"
            if makes
            else ""
        )
        return self.render("wrapup", made=more)

    def intake_path(self) -> Path:
        return self.dir / "intake.md"


def format_done(steps: list[Step]) -> str:
    lines = [
        f"- {plan.label(s)}: {s.summary}"
        for s in steps
        if s.state == "done" and s.summary and (s.kind == "agent" or s.kind in TOLD)
    ]
    return "\n".join(lines) or "(nothing yet: this is the first step)"


def format_feedback(notes: list[str]) -> str:
    if not notes:
        return "(none)"
    return "\n\n".join("\n".join(f"> {line}".rstrip() for line in note.splitlines()) for note in notes)
