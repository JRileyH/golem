"""Proposals: golem's own ideas for a project, for Riggy to say yes or no to.

A project's settings say how often golem looks (off, the default; hourly, daily or weekly).
When nothing is queued, running or parked anywhere, the scheduler starts a proposals job on
the live project most overdue for one (`due`). Like a question it only looks: `refresh`
brings the checkout up to date, a `propose` agent step looks the project over and returns
the one proposal it thinks best (MAX_PER_RUN) with its verdict, and golem's `proposals`
step posts it in the project's conversation, and opens an issue for it on its repo. Nothing
is committed, pushed or deployed.

Each job looks through one lens (features, security, polish…), the next in LENSES each time,
so that over time every side of the project gets a fresh look rather than the same kind of
idea again. The project's settings list every lens as a kind of proposal to turn off (a site
with no code worth tidying, a game that needs no new features…); all are on until he does,
and the turn skips the ones he turned off. The worker sees every earlier proposal with what
Riggy decided and why: to keep from repeating them and to learn what he values, not as a
template.

He gives each a thumbs up or down on the project's page, with a reason if he likes. A thumbs
up becomes a change job on the feature playbook, as soon as the project has no other change
under way, and that job's PR closes the proposal's issue; a thumbs down closes it with his
reason. The issues are only a record: golem never reads anything back from GitHub.

One at a time, so he gets a steady trickle to decide on rather than a pile: a project's turn
comes round every interval after its last proposals job (`turn`), and while as many proposals
await him as its settings allow (5 unless he says), its turns pass. Deciding on one brings the
next at the following turn, not straight away, and turns missed while he was away never pile up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .harness import VERDICT_SCHEMA
from .store import Job, Project, Proposal, Store

REQUEST = "Look at its {} for things to propose"  # a proposals job's request, naming its lens
INTERVALS = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(days=7)}
PRIORITY = -1000  # behind anything Riggy asks for, from the next step boundary
MAX_PER_RUN = 1  # a proposal a turn, the best the worker found
WAITING = (1, 2, 3, 4, 5, 6, 8, 10, 15, 20)  # how many may await Riggy, to pick from in a project's settings
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


def kinds(project: Project) -> tuple[Lens, ...]:
    """The kinds of proposal the project gets: every lens Riggy hasn't turned off."""
    off = set(project.proposals_off.split(","))
    return tuple(x for x in LENSES if x.name not in off)


def lens(store: Store, job: Job) -> Lens:
    """The job's lens, picked when it started (`start`) and named in its request."""
    for x in LENSES:
        if job.request == REQUEST.format(x.name):
            return x
    earlier = sum(1 for j in store.proposal_jobs(job.slug) if j.id < job.id)  # a lens since renamed
    return LENSES[earlier % len(LENSES)]


def next_lens(store: Store, project: Project) -> Lens | None:
    """The first kind still on after the lens the project's last proposals job looked through,
    in LENSES order; None if he turned them all off."""
    on, earlier = kinds(project), store.proposal_jobs(project.slug)
    after = LENSES.index(lens(store, earlier[-1])) + 1 if earlier else 0
    return next((x for x in LENSES[after:] + LENSES[:after] if x in on), None)


def turn(last: datetime | None, every: timedelta, at: datetime) -> datetime | None:
    """A project's latest turn for proposals by `at`: a whole number of intervals after its last
    proposals job (`last`), or `at` itself if it never had one; None before the first comes round."""
    if last is None:
        return at
    return last + (at - last) // every * every if at - last >= every else None


def due(store: Store, at: datetime, spare: bool = False) -> Project | None:
    """The project most overdue for proposals, when golem has nothing else to do: nothing
    queued or parked anywhere, and nothing running either unless a worker is `spare`. Only
    live, unpaused projects with no job of their own under way, a kind of proposal on and their
    turn come round count, and only if fewer proposals awaited Riggy at that turn than its
    settings allow, and still do: a turn at the limit passes, and the next is one later."""
    if store.jobs(("queued", "parked"), limit=1) or (not spare and store.jobs(("running",), limit=1)):
        return None
    found: list[tuple[datetime, Project]] = []
    for project, _ in store.live_projects():
        every = INTERVALS.get(project.proposals)
        if every is None or project.paused or store.active_job(project.slug) or not kinds(project):
            continue
        earlier = store.proposal_jobs(project.slug)
        last = datetime.fromisoformat(earlier[-1].created_at) if earlier else None
        if (latest := turn(last, every, at)) is None:
            continue
        waiting = (store.proposals_waiting(project.slug, t.isoformat(timespec="seconds")) for t in (latest, at))
        if max(waiting) < project.proposals_max:
            found.append((last or datetime.min.replace(tzinfo=UTC), project))
    return min(found, key=lambda f: f[0])[1] if found else None


def start(store: Store, project: Project) -> Job:
    """A proposals job for the project, through the next kind in turn (`due` checked one is on)."""
    request = REQUEST.format(next_lens(store, project).name)
    return store.add_job(project.slug, "change", request, "proposals", priority=PRIORITY)


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


def issue(p: Proposal, page: str) -> tuple[str, str]:
    """The title and body of the proposal's issue: a record on its repo of what golem proposed."""
    body = (
        f"{p.body}\n\n---\nProposed by golem (job {p.job_id}) while looking at the project's {p.lens}. "
        f"Riggy decides on it in golem: {page}. Nothing said here is read back."
    )
    return f"Proposal: {p.title}", body


def decision(p: Proposal) -> str:
    """Riggy's decision, for the proposal's issue."""
    said = "👍 Accepted" if p.state == "accepted" else "👎 Turned down"
    return said + (f": “{p.reason}”" if p.reason else ".") + (" golem builds it next; its PR closes this." if p.state == "accepted" else "")


def announcement(name: str, made: list[Proposal], page: str) -> str:
    """The proposal in the project's conversation (several only from a job that ran before one a
    turn), with where to decide on it."""
    if len(made) == 1:
        some, items = "a proposal", f"{made[0].title}\n{made[0].body}"
    else:
        some, items = f"{len(made)} proposals", "\n\n".join(f"{i}. {p.title}\n{p.body}" for i, p in enumerate(made, start=1))
    return f"I looked at its {name} and have {some} for you:\n\n{items}\n\nThumbs up or down (and why) on its page: {page}"
