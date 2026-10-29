"""Proposals: golem's own ideas for a project, for Riggy to say yes or no to.

A project's settings say how often golem looks (off, the default; hourly, daily or weekly).
When nothing is queued, running or parked anywhere, the scheduler starts a proposals job on
the live project most overdue for one (`due`). Like a question it only looks: `refresh`
brings the checkout up to date, a `propose` agent step looks the project over and returns
up to MAX_PER_RUN proposals with its verdict, and golem's `proposals` step posts them in
the project's conversation. Nothing is committed, pushed or deployed.

Each job looks through one lens (features, security, polish…), the next in LENSES each time,
so that over time every side of the project gets a fresh look rather than the same kind of
idea again. The worker sees every earlier proposal with what Riggy decided and why: to keep
from repeating them and to learn what he values, not as a template.

He gives each a thumbs up or down on the project's page, with a reason if he likes. A thumbs
up becomes a change job on the feature playbook, as soon as the project has no other change
under way. A project with MAX_OPEN proposals awaiting him gets no more until he decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .harness import VERDICT_SCHEMA
from .store import Job, Project, Proposal, Store

INTERVALS = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(days=7)}
PRIORITY = -1000  # behind anything Riggy asks for, from the next step boundary
MAX_PER_RUN = 3
MAX_OPEN = 5  # proposals awaiting Riggy before golem stops making more for the project
PAST_SHOWN = 60  # earlier proposals in the worker's prompt, newest first
SIZES = ("small", "medium", "large")


@dataclass(frozen=True)
class Lens:
    """One kind of improvement a proposals job looks for."""

    name: str
    focus: str  # what to look at, for the worker


# In turn, product and engineering alternating, so consecutive runs look at different things.
LENSES = (
    Lens("features", "What would make it more useful or more fun: a capability its users would reach for, built on what's there."),
    Lens("code health", "Duplication, tangled modules, dead code, unclear names, outdated dependencies: what would make the next change cheaper and safer."),
    Lens("user experience", "Where using it is confusing, slow to get through or awkward, above all on a phone: flows, layout, feedback, empty and error states."),
    Lens("security", "What a hostile visitor could do: input handling, authentication, secrets, headers, dependency advisories, what's exposed that shouldn't be."),
    Lens("performance", "Load time, bundle size, slow queries or renders, wasted requests, memory. Measure before proposing."),
    Lens("tests", "The paths that matter most and aren't covered, flaky or slow tests, what a regression would slip past."),
    Lens("polish", "The small details: wording, icons, transitions, page titles, favicon, sharing previews, what the first ten seconds feel like."),
    Lens("reliability", "What breaks under odd input, a restart, an empty or huge database or a slow network; errors nobody would notice; data that could be lost."),
    Lens("accessibility", "Keyboard use, screen readers, contrast, text size, reduced motion, touch target sizes."),
    Lens("developer experience", "How quickly a worker can understand, run, change and deploy it: AGENTS.md, scripts, docs, CI time, error messages."),
)  # fmt: skip

# The propose step's verdict: a step verdict, and when it's done, the proposals.
SCHEMA = {
    **VERDICT_SCHEMA,
    "properties": {
        **VERDICT_SCHEMA["properties"],
        "proposals": {
            "type": "array",
            "maxItems": MAX_PER_RUN,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The proposal in a few words"},
                    "what": {"type": "string", "description": "What to change, concretely enough to build from"},
                    "why": {"type": "string", "description": "What it gets him, and what you saw that made you propose it"},
                    "size": {"enum": list(SIZES)},
                },
                "required": ["title", "what", "why", "size"],
                "additionalProperties": False,
            },
        },
    },
}


def lens(store: Store, job: Job) -> Lens:
    """The job's lens: the next in turn after the project's earlier proposals jobs'."""
    earlier = sum(1 for j in store.proposal_jobs(job.slug) if j.id < job.id)
    return LENSES[earlier % len(LENSES)]


def due(store: Store, at: datetime) -> Project | None:
    """The project most overdue for proposals, when golem has nothing else to do: nothing
    queued, running or parked anywhere. Only live, unpaused projects with no job of their own
    under way and fewer than MAX_OPEN proposals awaiting Riggy count."""
    if store.jobs(("queued", "running", "parked"), limit=1):
        return None
    found: list[tuple[datetime, Project]] = []
    for project, _ in store.live_projects():
        every = INTERVALS.get(project.proposals)
        if every is None or project.paused or store.active_job(project.slug):
            continue
        if len(store.proposals(project.slug, ("open",))) >= MAX_OPEN:
            continue
        earlier = store.proposal_jobs(project.slug)
        last = datetime.fromisoformat(earlier[-1].created_at) if earlier else datetime.min.replace(tzinfo=UTC)
        if at - last >= every:
            found.append((last, project))
    return min(found, key=lambda f: f[0])[1] if found else None


def start(store: Store, project: Project) -> Job:
    """A proposals job for the project, through the next lens."""
    name = LENSES[len(store.proposal_jobs(project.slug)) % len(LENSES)].name  # what lens() will say for it
    return store.add_job(project.slug, "change", f"Look at its {name} for things to propose", "proposals", priority=PRIORITY)


def collect(raw: object) -> list[tuple[str, str]]:
    """The (title, body) of each proposal a worker returned, up to MAX_PER_RUN."""
    found = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or len(found) == MAX_PER_RUN:
            continue
        title, what, why = (" ".join(str(item.get(k) or "").split()) for k in ("title", "what", "why"))
        if not title or not what:
            continue
        size = item.get("size") if item.get("size") in SIZES else ""
        body = "\n\n".join(p for p in (what[:1500], f"Why: {why[:800]}" if why else "", f"Size: {size}." if size else "") if p)
        found.append((title[:100], body))
    return found


def past(proposals: list[Proposal]) -> str:
    """Earlier proposals for the worker's prompt: what each was and what Riggy made of it."""
    said = {"open": "not decided yet", "accepted": "accepted", "rejected": "turned down"}
    lines = []
    for p in proposals[:PAST_SHOWN]:
        why = f": “{' '.join(p.reason.split())}”" if p.reason else ""
        lines.append(f"- [{p.lens}] {p.title} — {said.get(p.state, p.state)}{why}")
    return "\n".join(lines) or "(none yet: these are the first)"


def request(p: Proposal) -> str:
    """The change job an accepted proposal becomes: its first line is the job's title."""
    why = f": “{p.reason}”" if p.reason else "."
    return f"{p.title}\n\n{p.body}\n\n(golem proposed this while looking at {p.lens}; Riggy accepted it{why})"


def announcement(name: str, made: list[Proposal], page: str) -> str:
    """The proposals in the project's conversation, with where to decide on them."""
    items = "\n\n".join(f"{i}. {p.title}\n{p.body}" for i, p in enumerate(made, start=1))
    some = "a proposal" if len(made) == 1 else f"{len(made)} proposals"
    return f"I looked at its {name} and have {some} for you:\n\n{items}\n\nThumbs up or down (and why) on its page: {page}"
