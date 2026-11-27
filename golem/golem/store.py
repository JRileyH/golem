"""SQLite: the project registry, the job queue and each job's steps, Riggy's notes to jobs,
run records, the conversation (talk.py), and reports about golem itself (reports.py).

One process owns the database and every call is short, so a single connection is enough.
"""

from __future__ import annotations

import sqlite3
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .reports import worst

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT '',  -- the mode (plan.MODES) its requests run in unless they ask for another: Riggy's setting
    model TEXT NOT NULL DEFAULT 'opus',
    effort TEXT NOT NULL DEFAULT 'high',
    paused INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    tag TEXT NOT NULL DEFAULT '',    -- what sort of project it is (tags), '' for none
    proposals TEXT NOT NULL DEFAULT 'off',  -- how often golem looks for improvements to propose (EVERY)
    proposals_max INTEGER NOT NULL DEFAULT 5,  -- proposals awaiting Riggy before golem stops making more
    proposals_off TEXT NOT NULL DEFAULT '',  -- kinds of proposal (proposals.LENSES) he turned off, comma-separated
    parallel_jobs INTEGER NOT NULL DEFAULT 1, -- changes that may be under way at once, taking turns (turns.py)
    vacuum INTEGER NOT NULL DEFAULT 0 -- 1: it runs as on a fresh golem, sharing nothing with other projects (Settings.lessons, job_claude)
);
-- The few big-picture sorts of project ("Game"), shown on the projects page in their color.
CREATE TABLE IF NOT EXISTS tags (
    name TEXT PRIMARY KEY,
    color INTEGER NOT NULL,          -- 1-8, the projects page's palette
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL REFERENCES projects(slug),
    kind TEXT NOT NULL,              -- new | change
    request TEXT NOT NULL,           -- Riggy's words that started the job
    playbook TEXT NOT NULL,
    phase INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 0,
    resume_session TEXT,             -- set when a blocked step should continue its session
    attempts INTEGER NOT NULL DEFAULT 0,
    retry_at TEXT,
    issue INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',  -- what it's about in a few words ("Quiz Mode"); '' for none (plan.title)
    worktree INTEGER NOT NULL DEFAULT 0, -- 1: it works in a tree of its own (workspace.TREES); 0: in the project's checkout
    start TEXT NOT NULL DEFAULT 'change', -- where it came from and what it may do (STARTS)
    workflow TEXT NOT NULL DEFAULT '',   -- the workflow it runs, by key ('' from before workflows)
    mode TEXT NOT NULL DEFAULT '',       -- the mode it runs in (plan.MODES); '' for the usual way
    graph TEXT NOT NULL DEFAULT '',      -- its own flattened copy of the workflow (engine.flatten), from when it started; '' before
    graph_hash TEXT NOT NULL DEFAULT '', -- the workflow's digest when it was copied, to tell whether it's changed since
    vars TEXT NOT NULL DEFAULT '{}'      -- JSON: its counters, settings for blocks still to come, the loop guard (engine.blank_vars)
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    text TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    step_id INTEGER,                 -- for one step rather than the next
    node_id TEXT NOT NULL DEFAULT '' -- for one block of the workflow rather than the next (a check-in's reply_to)
);
-- The text of each part of a stage block (stages.py): golem's, or Riggy's once he's changed it.
CREATE TABLE IF NOT EXISTS stage_texts (
    stage TEXT NOT NULL,
    part TEXT NOT NULL,              -- base, plan, <setting>.<option> or care.<level>
    text TEXT NOT NULL,
    seeded TEXT NOT NULL DEFAULT '', -- the digest of golem's text it came from
    edited INTEGER NOT NULL DEFAULT 0,
    upstream TEXT NOT NULL DEFAULT '',  -- golem's newer text's digest, once he's changed his
    sig TEXT NOT NULL DEFAULT '',    -- golem's signature: changed outside the web app, golem's text is used
    updated_at TEXT NOT NULL,
    PRIMARY KEY (stage, part)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    phase TEXT NOT NULL,
    model TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT,
    session_id TEXT,
    num_turns INTEGER,
    cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    log_path TEXT
);
-- A job's steps, in order: each a visit to a block of its workflow (engine.py), written as the
-- job gets there, and the output it left by. Loops come back to a block: its visits count up.
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- agent, script, or one of golem's own (engine.CODE, checkin, choose, counter, condition, alert)
    name TEXT NOT NULL,
    max_turns INTEGER,
    optional INTEGER NOT NULL DEFAULT 0,  -- Riggy may skip it
    state TEXT NOT NULL DEFAULT 'pending',
    summary TEXT,                    -- what it did, or what it's waiting for
    problem TEXT,                    -- a fix step's: what to fix
    error TEXT,                      -- why the last attempt failed
    links TEXT NOT NULL DEFAULT '{}',  -- JSON {label: url}: the PR, commit, CI run, site
    started_at TEXT,
    ended_at TEXT,
    model TEXT,                      -- Riggy's choice for this step, over the defaults
    effort TEXT,
    options TEXT NOT NULL DEFAULT '[]', -- JSON: an options step's options (options.py)
    design TEXT NOT NULL DEFAULT '{}',  -- JSON: a design round's doc, pages, questions and pick (options.Design)
    turn TEXT NOT NULL DEFAULT '',      -- the turn it takes on its project: build | ship | none; '' from before (turns.py)
    node_id TEXT NOT NULL DEFAULT '',   -- the block of the job's workflow it's a visit to (engine.py); '' from before
    port TEXT NOT NULL DEFAULT '',      -- the output it left by, once it's done
    visit INTEGER NOT NULL DEFAULT 0,   -- which visit to its block it is: 1, 2… (loops come back)
    head TEXT NOT NULL DEFAULT '',      -- the job's branch's commit once it was done, for what came since (about.py)
    item TEXT NOT NULL DEFAULT '{}'     -- unused: a checklist item's (checklist mode, gone), kept with the jobs that had them
);
-- What the projects page shows for a live project (cards.py).
CREATE TABLE IF NOT EXISTS cards (
    slug TEXT PRIMARY KEY,
    tagline TEXT NOT NULL,
    description TEXT NOT NULL,
    kind TEXT NOT NULL,              -- game | app | tool | site: says what its button does
    image TEXT,                      -- screenshot file name in <state>/cards, once one's taken
    updated_at TEXT NOT NULL
);
-- Everything Riggy and golem say to each other (talk.py).
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,           -- a project's slug, or inbox | status
    who TEXT NOT NULL,               -- riggy | golem
    text TEXT NOT NULL,
    image TEXT,                      -- a picture's URL on the status page
    notify INTEGER NOT NULL DEFAULT 0,  -- golem wanted his attention: a question, a check-in, done
    at TEXT NOT NULL,
    job_id INTEGER,                  -- the job it's about: its activity (none for golem-wide news, or from before)
    step_id INTEGER                  -- and the step, if one: where a notification opens
);
CREATE INDEX IF NOT EXISTS messages_by_channel ON messages (channel, id);
-- Riggy's devices that get push notifications (push.py): each browser's Web Push subscription.
CREATE TABLE IF NOT EXISTS subscriptions (
    endpoint TEXT PRIMARY KEY,
    keys TEXT NOT NULL,              -- JSON {p256dh, auth}
    label TEXT NOT NULL DEFAULT '',  -- which device, from its browser
    created_at TEXT NOT NULL
);
-- The model and effort for each kind of agent step (its name: spec, docs, fix…), set on
-- the status page. Empty means not set here. The name '*' is every other kind.
CREATE TABLE IF NOT EXISTS defaults (
    name TEXT PRIMARY KEY,
    model TEXT NOT NULL DEFAULT '',
    effort TEXT NOT NULL DEFAULT ''
);
-- Improvements golem proposed for a project while it had nothing else to do (proposals.py),
-- and what Riggy made of them. An accepted one becomes a change job.
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL REFERENCES projects(slug),
    job_id INTEGER NOT NULL REFERENCES jobs(id),  -- the proposals job that made it
    lens TEXT NOT NULL,              -- the kind of improvement it was looking for (proposals.LENSES)
    title TEXT NOT NULL,
    body TEXT NOT NULL,              -- the whole of it as plain text: what, why, how big, how to build it
    card TEXT NOT NULL DEFAULT '{}', -- JSON: its fields for the card he decides from (proposals.collect); {} before cards
    state TEXT NOT NULL DEFAULT 'open',  -- open | accepted | rejected | dismissed (off his list with no feedback: workers never see it)
    reason TEXT NOT NULL DEFAULT '', -- Riggy's why, if he gave one
    built_by INTEGER,                -- the change job an accepted one became, once it's queued
    created_at TEXT NOT NULL,
    decided_at TEXT,
    issue INTEGER                    -- its GitHub issue: a record, never read back
);
-- Changes on one project that ship together (shipments.py): one job's ship steps carry them all.
CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL,
    carrier INTEGER NOT NULL,        -- the job whose ship steps ship them all
    jobs TEXT NOT NULL,              -- JSON [[job id, step id]…], the carrier first: each job and the step its run of ship steps began at
    state TEXT NOT NULL DEFAULT 'shipping',  -- shipping | shipped | dropped (the carrier stopped before it shipped)
    created_at TEXT NOT NULL,
    ended_at TEXT
);
-- Reports about golem itself (reports.py): workers' (`golem report`) and golem's own incidents,
-- for golem's triage to turn into proposals for golem. No foreign keys: they outlive their projects.
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,              -- agent | golem
    kind TEXT NOT NULL,                -- reports.KINDS
    impact TEXT NOT NULL,              -- blocked | costly | minor: for an incident, the worst it's been
    summary TEXT NOT NULL,             -- one line
    detail TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',  -- what makes golem's incidents the same one; '' for a worker's
    count INTEGER NOT NULL DEFAULT 1,  -- how many times it's happened
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    job_id INTEGER,                    -- where it happened (the latest time): job, step and run
    step_id INTEGER,
    run_id INTEGER,
    slug TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',    -- the step, as Riggy sees it
    model TEXT NOT NULL DEFAULT '',    -- the run's engine, for a worker's
    version TEXT NOT NULL DEFAULT '',  -- golem's commit then
    state TEXT NOT NULL DEFAULT 'new', -- new | triaged | accepted | dismissed (reports.STATES)
    proposal_id INTEGER,               -- the proposal for golem that cites it
    triage_job INTEGER,                -- the triage job that read it
    note TEXT NOT NULL DEFAULT ''      -- triage's word on it, for one it didn't make a proposal of
);
CREATE INDEX IF NOT EXISTS reports_by_state ON reports (state, id);
CREATE INDEX IF NOT EXISTS reports_by_fingerprint ON reports (fingerprint);
"""

# Columns added after the first release, added to older databases on open.
MIGRATIONS = {
    "projects": {
        "model": "TEXT NOT NULL DEFAULT 'opus'",
        "effort": "TEXT NOT NULL DEFAULT 'high'",
        "tag": "TEXT NOT NULL DEFAULT ''",
        "proposals": "TEXT NOT NULL DEFAULT 'off'",
        "proposals_max": "INTEGER NOT NULL DEFAULT 5",
        "proposals_off": "TEXT NOT NULL DEFAULT ''",
        "parallel_jobs": "INTEGER NOT NULL DEFAULT 1",
        "vacuum": "INTEGER NOT NULL DEFAULT 0",
    },
    # Jobs from before trees have 0 and finish in the project's checkout, where their work is.
    "jobs": {
        "title": "TEXT NOT NULL DEFAULT ''",
        "worktree": "INTEGER NOT NULL DEFAULT 0",
        "start": "TEXT NOT NULL DEFAULT 'change'",
        "workflow": "TEXT NOT NULL DEFAULT ''",
        "mode": "TEXT NOT NULL DEFAULT ''",
        "graph": "TEXT NOT NULL DEFAULT ''",
        "graph_hash": "TEXT NOT NULL DEFAULT ''",
        "vars": "TEXT NOT NULL DEFAULT '{}'",
    },
    "proposals": {"issue": "INTEGER", "card": "TEXT NOT NULL DEFAULT '{}'"},
    "runs": {"step_id": "INTEGER", "glance": "TEXT NOT NULL DEFAULT ''"},  # glance: where its time went (glance.py), once it ended
    "steps": {
        "optional": "INTEGER NOT NULL DEFAULT 0",
        "model": "TEXT",
        "effort": "TEXT",
        "options": "TEXT NOT NULL DEFAULT '[]'",
        "design": "TEXT NOT NULL DEFAULT '{}'",
        "turn": "TEXT NOT NULL DEFAULT ''",
        "node_id": "TEXT NOT NULL DEFAULT ''",
        "port": "TEXT NOT NULL DEFAULT ''",
        "visit": "INTEGER NOT NULL DEFAULT 0",
        "head": "TEXT NOT NULL DEFAULT ''",
        "item": "TEXT NOT NULL DEFAULT '{}'",  # unused (checklist mode, gone): kept with the jobs that had them
    },
    "messages": {"job_id": "INTEGER", "step_id": "INTEGER"},
    "notes": {"step_id": "INTEGER", "node_id": "TEXT NOT NULL DEFAULT ''"},  # a note for one step (or block) rather than the next
}
# Columns golem no longer uses, dropped from older databases on open.
DROPPED = {"messages": ("via",), "cards": ("tags",)}
GAME = "Game"  # the one tag golem starts with
TAG_COLOR = 7

# Jobs in these states hold or want the worker slot, or wait on Riggy; anything else is finished.
ACTIVE = ("queued", "running", "waiting", "parked")
FINISHED = ("done", "failed", "killed")
# Steps in these states are still to do: the first of them is the job's current step.
OPEN = ("pending", "running", "waiting")
# Where a job comes from, which says what it may do: a new project from the inbox, a change to
# one from its conversation, a question about one (look), golem's own look for improvements
# (propose), taking on a repo golem didn't make (adopt). `sub` is for workflows used only inside
# others, never a job's own.
STARTS = ("new", "change", "look", "propose", "adopt")
# golem's triage of the reports about itself (triage.py): a job's start too, but none of a
# workflow's, so no workflow can start one or be one.
TRIAGE = "triage"
# Jobs that only look: nothing is committed, pushed or deployed, so they can run between the
# steps of a project's change.
LOOKS = ("look", "propose", TRIAGE)
EVERY = ("off", "hourly", "daily", "weekly")  # how often a project gets proposals
# golem's own project, hidden from every list of projects (its slug is reserved: intake.RESERVED):
# where its jobs about itself belong, its triage of reports about golem (triage.py).
INTERNAL = "golem"
MODELS = ("fable", "opus", "sonnet", "haiku")
MAX_WORKERS = 8  # steps that can run at once, across projects (the workers setting, kv `workers`)
CHANGES_AT_ONCE = (1, 2, 3, 4)  # what a project's parallel_jobs can be
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def later(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat(timespec="seconds")


@dataclass
class Project:
    slug: str
    name: str
    summary: str
    mode: str
    model: str  # "" for each step's default (plan.engine)
    effort: str
    paused: bool
    created_at: str
    tag: str = ""  # a Tag's name, or "" for none
    proposals: str = "off"  # how often golem looks for improvements to propose (EVERY)
    proposals_max: int = 5  # proposals awaiting Riggy before golem stops making more
    # Kinds of proposal he turned off, comma-separated (proposals.LENSES): every kind is on
    # until he turns it off, new ones too.
    proposals_off: str = ""
    # Changes that may be under way at once; they take turns at building and ship together (turns.py, shipments.py).
    parallel_jobs: int = 1
    # It runs as on a fresh golem: lessons of its own from nothing, and each job a Claude Code of its own
    # with nothing in it (Scheduler.lessons_path, worker_env).
    vacuum: bool = False


@dataclass
class Proposal:
    """An improvement golem proposed for a project, and what Riggy made of it."""

    id: int
    slug: str
    job_id: int
    lens: str
    title: str
    body: str
    state: str  # open | accepted | rejected | dismissed (off his list with no feedback: workers never see it)
    reason: str
    built_by: int | None  # the change job building it, once accepted and queued
    created_at: str
    decided_at: str | None
    issue: int | None = None  # its GitHub issue, a record of it there: golem never reads it back
    card: str = "{}"  # JSON: its fields for the card he decides from (proposals.collect); {} before cards


@dataclass
class Report:
    """Something that got in the way of golem's work, about golem itself (reports.py)."""

    id: int
    source: str  # agent | golem
    kind: str
    impact: str
    summary: str
    detail: str
    fingerprint: str
    count: int
    first_seen: str
    last_seen: str
    job_id: int | None
    step_id: int | None
    run_id: int | None
    slug: str
    label: str
    model: str
    version: str
    state: str  # new | triaged | accepted | dismissed
    proposal_id: int | None
    triage_job: int | None
    note: str


# Where a report came from: what golem knows about a run or step, filled in by golem, never the worker.
REPORT_PLACE = ("job_id", "step_id", "run_id", "slug", "label", "model", "version")


@dataclass
class Shipment:
    """Changes on one project shipping together (shipments.py): the carrier's ship steps ship them
    all, and the others' go by as done once it has."""

    id: int
    slug: str
    carrier: int  # the job whose ship steps ship them all
    jobs: dict[int, int]  # each job it ships, the carrier first: the step its run of ship steps began at
    state: str  # shipping | shipped | dropped
    created_at: str
    ended_at: str | None = None

    @property
    def riders(self) -> list[int]:
        """The jobs it ships besides the carrier, oldest first."""
        return [j for j in self.jobs if j != self.carrier]


@dataclass
class Tag:
    """A big-picture sort of project ("Game"); each project has one or none."""

    name: str
    color: int  # 1-8
    created_at: str = ""


@dataclass
class Card:
    slug: str
    tagline: str
    description: str
    kind: str
    image: str | None = None
    updated_at: str = ""


@dataclass
class Said:
    """One message in the conversation."""

    id: int
    channel: str
    who: str  # riggy | golem
    text: str
    image: str | None
    notify: bool
    at: str
    job_id: int | None = None  # the job it's about, for its activity
    step_id: int | None = None


@dataclass
class Job:
    id: int
    slug: str
    kind: str
    request: str
    playbook: str
    phase: int
    state: str
    priority: int
    resume_session: str | None
    attempts: int
    retry_at: str | None
    issue: int | None
    created_at: str
    updated_at: str
    title: str = ""  # what it's about in a few words ("Quiz Mode"); plan.title has one for every job
    worktree: int = 0  # 1: it works in a git worktree of its own (Scheduler.tree); 0: in the project's checkout
    start: str = "change"  # where it came from (STARTS)
    workflow: str = ""  # the workflow it runs, by key; "" from before workflows
    mode: str = ""  # the mode it runs in (plan.MODES); "" for the usual way
    graph: str = ""  # JSON: its own flattened copy (engine.flatten), kept as its record; "" until it starts
    graph_hash: str = ""
    vars: str = "{}"  # JSON: counters, settings for blocks still to come, the loop guard (engine.blank_vars)


# A job's columns but those only its own page needs (its graph and vars, which can be large): for listing many.
LISTED = ", ".join(f.name for f in fields(Job) if f.name not in ("graph", "graph_hash", "vars"))


@dataclass
class Step:
    id: int  # 0 for steps a job will get but hasn't yet (see plan.planned)
    job_id: int
    position: int
    kind: str
    name: str
    max_turns: int | None = None
    optional: bool = False
    state: str = "pending"  # pending | running | waiting | done | skipped | failed
    summary: str | None = None
    problem: str | None = None
    error: str | None = None
    links: dict[str, str] = field(default_factory=dict)
    started_at: str | None = None
    ended_at: str | None = None
    model: str | None = None  # Riggy's choice for this step; None for the defaults
    effort: str | None = None
    options: list[dict] = field(default_factory=list)  # an options step's, as options.Option fields
    design: dict = field(default_factory=dict)  # a design step's round, as options.Design fields
    turn: str = ""  # the turn it takes on its project: build | ship | none; "" if not written down (plan.turn_of)
    node_id: str = ""  # the block of the job's workflow it's a visit to; "" from before workflows
    port: str = ""  # the output it left by, once done (engine.advance)
    visit: int = 0  # which visit to its block: loops come back
    head: str = ""  # the job's branch's commit once it was done ("" if it touches no tree, or from before)


class Store:
    @classmethod
    def read_only(cls, path: Path | str) -> Store:
        """The database as it is, for reading only: no schema or migrations, so a golem of another
        version can go on using it meanwhile, its work undisturbed (compare.py)."""
        store = cls.__new__(cls)
        store.db = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
        store.db.row_factory = sqlite3.Row
        return store

    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            have = self._columns(table)
            for column, decl in columns.items():
                if column not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self.db.execute("CREATE INDEX IF NOT EXISTS messages_by_job ON messages (job_id, id)")
        if self.get("tags") is None:
            # Cards used to carry a few tags each, written by the card's model. Only "Game" said
            # much, so it's the one tag to start with: on every project whose card was a game.
            self.db.execute("INSERT OR IGNORE INTO tags (name, color, created_at) VALUES (?, ?, ?)", (GAME, TAG_COLOR, now()))
            if "tags" in self._columns("cards"):
                self.db.execute(
                    "UPDATE projects SET tag = ? WHERE slug IN (SELECT slug FROM cards WHERE kind = 'game' OR tags LIKE ?)",
                    (GAME, f'%"{GAME}"%'),
                )
            self.put("tags", "1")
        for table, columns in DROPPED.items():
            for column in self._columns(table) & set(columns):
                self.db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        if self.get("job_starts") is None:
            # Jobs said what they were only by their kind and playbook.
            self.db.execute(
                "UPDATE jobs SET start = CASE WHEN kind IN ('new', 'adopt') THEN kind WHEN playbook = 'question' THEN 'look'"
                " WHEN playbook = 'proposals' THEN 'propose' ELSE 'change' END"
            )
            self.put("job_starts", "1")
        if self.get("project_modes") is None:
            # Modes became Riggy's to make (workflows): "standard" was every workflow's default tab.
            self.db.execute("UPDATE projects SET mode = '' WHERE mode = 'standard'")
            self.put("project_modes", "1")
        if self.get("project_engines") is None:
            # Projects used to always name a model and effort, opus and high unless Riggy said
            # otherwise. Those now come from each step's defaults, which start out the same.
            self.db.execute("UPDATE projects SET model = '' WHERE model = 'opus'")
            self.db.execute("UPDATE projects SET effort = '' WHERE effort = 'high'")
            self.put("project_engines", "1")
        if self.get("workflow_files") is None:
            # Workflows were kept here, for Riggy to edit; now golem reads its own from its files.
            self.db.execute("DROP TABLE IF EXISTS workflow_tabs")
            self.db.execute("DROP TABLE IF EXISTS workflows")
            for key in ("workflow_key", "modes_seeded", *(f"fallback_{s}" for s in STARTS)):
                self.db.execute("DELETE FROM kv WHERE key = ?", (key,))
            self.put("workflow_files", "1")
        if self.get("golems_modes") is None:
            # Modes were Riggy's to make, each a tab of a workflow; now they're golem's (plan.MODES),
            # and none of the old ones is among them.
            self.db.execute("DROP TABLE IF EXISTS modes")
            self.db.execute("UPDATE projects SET mode = ''")
            self.put("golems_modes", "1")

    def _columns(self, table: str) -> set[str]:
        return {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}

    # --- kv -------------------------------------------------------------------

    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def put(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # --- projects -------------------------------------------------------------

    def add_project(
        self, slug: str, name: str, summary: str, mode: str = "", model: str = "", effort: str = ""
    ) -> Project:
        self.db.execute(
            "INSERT INTO projects (slug, name, summary, mode, model, effort, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (slug, name, summary, mode, model, effort, now()),
        )
        return self.project(slug)

    def project(self, slug: str) -> Project | None:
        row = self.db.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
        return _project(row) if row else None

    def projects(self) -> list[Project]:
        """Riggy's projects, the latest first: not golem's own (INTERNAL)."""
        return [_project(r) for r in self.db.execute("SELECT * FROM projects WHERE slug != ? ORDER BY created_at DESC", (INTERNAL,))]

    def internal_project(self) -> Project:
        """golem's own project (INTERNAL), made the first time it's wanted: where golem's jobs about
        itself belong (its triage, triage.py), since every job, run and proposal has a project."""
        self.db.execute(
            "INSERT OR IGNORE INTO projects (slug, name, summary, model, effort, created_at) VALUES (?, ?, ?, '', '', ?)",
            (INTERNAL, "golem", "golem itself: its jobs about how golem works", now()),
        )
        return self.project(INTERNAL)

    def set_paused(self, slug: str, paused: bool) -> None:
        self.db.execute("UPDATE projects SET paused = ? WHERE slug = ?", (int(paused), slug))

    def update_project(self, slug: str, **fields: str | int) -> Project:
        assert set(fields) <= {"name", "mode", "model", "effort", "tag", "proposals", "proposals_max", "proposals_off", "parallel_jobs", "vacuum"}, fields
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE projects SET {columns} WHERE slug = ?", (*fields.values(), slug))
        return self.project(slug)

    def move_project(self, old: str, new: str) -> Project:
        """Give a project a new slug, keeping its jobs."""
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute(
                "INSERT INTO projects (slug, name, summary, mode, model, effort, paused, created_at, tag, proposals, proposals_max, proposals_off, parallel_jobs, vacuum)"
                " SELECT ?, name, summary, mode, model, effort, paused, created_at, tag, proposals, proposals_max, proposals_off, parallel_jobs, vacuum"
                " FROM projects WHERE slug = ?",
                (new, old),
            )
            self.db.execute("UPDATE jobs SET slug = ? WHERE slug = ?", (new, old))
            self.db.execute("UPDATE proposals SET slug = ? WHERE slug = ?", (new, old))
            self.db.execute("UPDATE shipments SET slug = ? WHERE slug = ?", (new, old))
            self.db.execute("UPDATE cards SET slug = ? WHERE slug = ?", (new, old))
            self.db.execute("UPDATE messages SET channel = ? WHERE channel = ?", (new, old))
            self.db.execute("DELETE FROM projects WHERE slug = ?", (old,))
        return self.project(new)

    def delete_project(self, slug: str) -> list[int]:
        """Remove a project with its jobs, notes and runs. Returns the job ids (their logs go too)."""
        jobs = "SELECT id FROM jobs WHERE slug = ?"
        with self.db:
            self.db.execute("BEGIN")
            ids = [r["id"] for r in self.db.execute(jobs, (slug,))]
            self.db.execute(f"DELETE FROM notes WHERE job_id IN ({jobs})", (slug,))
            self.db.execute(f"DELETE FROM runs WHERE job_id IN ({jobs})", (slug,))
            self.db.execute(f"DELETE FROM steps WHERE job_id IN ({jobs})", (slug,))
            self.db.execute("DELETE FROM proposals WHERE slug = ?", (slug,))
            self.db.execute("DELETE FROM shipments WHERE slug = ?", (slug,))
            self.db.execute("DELETE FROM jobs WHERE slug = ?", (slug,))
            self.db.execute("DELETE FROM cards WHERE slug = ?", (slug,))
            self.db.execute("DELETE FROM messages WHERE channel = ?", (slug,))
            self.db.execute("DELETE FROM projects WHERE slug = ?", (slug,))
        return ids

    def import_jobs(self, jobs: list[tuple[Job, list[Step]]]) -> list[int]:
        """A project's past jobs as GitHub remembers them (history.py), finished, with their steps,
        under new numbers: the numbers in their issues were an earlier golem's. Returns the new ids."""
        ids = []
        with self.db:
            self.db.execute("BEGIN")
            for job, steps in jobs:
                row = {k: v for k, v in asdict(job).items() if k != "id"}
                cur = self.db.execute(f"INSERT INTO jobs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
                ids.append(cur.lastrowid)
                self.db.executemany(
                    "INSERT INTO steps (job_id, position, kind, name, state, summary, links) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(cur.lastrowid, i, s.kind, s.name, s.state, s.summary, json.dumps(s.links)) for i, s in enumerate(steps, start=1)],
                )
        return ids

    def live_projects(self) -> list[tuple[Project, str]]:
        """Projects with a site: deployed by a step, or with a finished job (from before steps
        were tracked). With when they last went live, latest first."""
        rows = self.db.execute(
            "SELECT projects.*, MAX(COALESCE(steps.ended_at, jobs.updated_at)) AS live_at FROM projects"
            " JOIN jobs USING (slug) LEFT JOIN steps ON steps.job_id = jobs.id AND steps.kind = 'deploy' AND steps.state = 'done'"
            " WHERE (steps.id IS NOT NULL OR jobs.state = 'done') AND projects.slug != ? GROUP BY projects.slug ORDER BY live_at DESC",
            (INTERNAL,),
        )
        found = []
        for r in rows:
            data = dict(r)
            live_at = data.pop("live_at")
            found.append((_project(data), live_at))
        return found

    # --- tags -----------------------------------------------------------------

    def tags(self) -> list[Tag]:
        """Oldest first, so "Game" leads."""
        return [Tag(**r) for r in self.db.execute("SELECT * FROM tags ORDER BY rowid")]

    def tag(self, name: str) -> Tag | None:
        """By name, any case."""
        row = self.db.execute("SELECT * FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        return Tag(**row) if row else None

    def add_tag(self, name: str, color: int = 0) -> Tag:
        """A new tag, in `color` or the first color no tag has yet."""
        if not color:
            used = {t.color for t in self.tags()}
            color = next((c for c in range(1, 9) if c not in used), len(used) % 8 + 1)
        self.db.execute("INSERT INTO tags (name, color, created_at) VALUES (?, ?, ?)", (name, color, now()))
        return self.tag(name)

    def update_tag(self, name: str, new_name: str | None = None, color: int | None = None) -> Tag:
        """Rename a tag (its projects keep it) or give it a new color."""
        with self.db:
            self.db.execute("BEGIN")
            if color is not None:
                self.db.execute("UPDATE tags SET color = ? WHERE name = ?", (color, name))
            if new_name is not None and new_name != name:
                self.db.execute("UPDATE tags SET name = ? WHERE name = ?", (new_name, name))
                self.db.execute("UPDATE projects SET tag = ? WHERE tag = ?", (new_name, name))
        return self.tag(new_name or name)

    def delete_tag(self, name: str) -> None:
        """The tag goes, and its projects have none."""
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("UPDATE projects SET tag = '' WHERE tag = ?", (name,))
            self.db.execute("DELETE FROM tags WHERE name = ?", (name,))

    def tag_counts(self) -> dict[str, int]:
        """{tag: how many projects have it}."""
        return {r["tag"]: r["n"] for r in self.db.execute("SELECT tag, COUNT(*) AS n FROM projects WHERE tag != '' GROUP BY tag")}

    # --- cards ----------------------------------------------------------------

    def card(self, slug: str) -> Card | None:
        row = self.db.execute("SELECT slug, tagline, description, kind, image, updated_at FROM cards WHERE slug = ?", (slug,)).fetchone()
        return Card(**row) if row else None

    def put_card(self, card: Card) -> Card:
        self.db.execute(
            "INSERT OR REPLACE INTO cards (slug, tagline, description, kind, image, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (card.slug, card.tagline, card.description, card.kind, card.image, now()),
        )
        return self.card(card.slug)

    # --- the conversation -----------------------------------------------------

    def add_message(
        self, channel: str, who: str, text: str, image: str | None = None, notify: bool = False,
        job_id: int | None = None, step_id: int | None = None,
    ) -> Said:  # fmt: skip
        cur = self.db.execute(
            "INSERT INTO messages (channel, who, text, image, notify, at, job_id, step_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (channel, who, text, image, int(notify), now(), job_id, step_id),
        )
        return self.messages(channel, after=cur.lastrowid - 1, limit=1)[0]

    def job_messages(self, job_id: int, limit: int = 200) -> list[Said]:
        """A job's activity: what golem and Riggy said about it, oldest first (its latest `limit`)."""
        rows = self.db.execute("SELECT * FROM messages WHERE job_id = ? ORDER BY id DESC LIMIT ?", (job_id, limit)).fetchall()[::-1]
        return [Said(**{**dict(r), "notify": bool(r["notify"])}) for r in rows]

    def messages(self, channel: str, limit: int = 200, after: int = 0) -> list[Said]:
        """A channel's latest messages (or the first after id `after`), oldest first."""
        if after:
            rows = self.db.execute(
                "SELECT * FROM messages WHERE channel = ? AND id > ? ORDER BY id LIMIT ?", (channel, after, limit)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM messages WHERE channel = ? ORDER BY id DESC LIMIT ?", (channel, limit)
            ).fetchall()[::-1]
        return [Said(**{**dict(r), "notify": bool(r["notify"])}) for r in rows]

    # --- push subscriptions ---------------------------------------------------

    def add_subscription(self, endpoint: str, keys: dict[str, str], label: str = "") -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO subscriptions (endpoint, keys, label, created_at) VALUES (?, ?, ?, ?)",
            (endpoint, json.dumps(keys), label, now()),
        )

    def subscriptions(self) -> list[dict]:
        """In the form pywebpush takes: {endpoint, keys: {p256dh, auth}}, with the device's label."""
        rows = self.db.execute("SELECT * FROM subscriptions ORDER BY created_at")
        return [{"endpoint": r["endpoint"], "keys": json.loads(r["keys"]), "label": r["label"]} for r in rows]

    def forget_subscription(self, endpoint: str) -> None:
        self.db.execute("DELETE FROM subscriptions WHERE endpoint = ?", (endpoint,))

    # --- jobs -----------------------------------------------------------------

    def add_job(
        self, slug: str, kind: str, request: str, playbook: str, priority: int = 0, title: str = "", start: str = ""
    ) -> Job:
        """A new job, which works in a tree of its own (Job.worktree)."""
        start = start or start_of(kind, playbook)
        assert start in (*STARTS, TRIAGE), start
        cur = self.db.execute(
            "INSERT INTO jobs (slug, kind, request, playbook, priority, created_at, updated_at, title, worktree, start)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (slug, kind, request, playbook, priority, now(), now(), title, start),
        )
        return self.job(cur.lastrowid)

    def job(self, job_id: int) -> Job | None:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job(**row) if row else None

    def update_job(self, job_id: int, **fields) -> Job:
        """Change a job. Its copy of its workflow stays with it once it's over: its record of how it went."""
        if isinstance(fields.get("vars"), dict):
            fields["vars"] = json.dumps(fields["vars"])
        fields["updated_at"] = now()
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))
        return self.job(job_id)

    def change_jobs(self, slug: str) -> list[Job]:
        """The project's unfinished jobs that change it (not questions or proposals), oldest first."""
        marks, looks = ", ".join("?" * len(ACTIVE)), ", ".join("?" * len(LOOKS))
        rows = self.db.execute(
            f"SELECT * FROM jobs WHERE slug = ? AND state IN ({marks}) AND start NOT IN ({looks}) ORDER BY id",
            (slug, *ACTIVE, *LOOKS),
        )
        return [Job(**r) for r in rows]

    def room_for_change(self, slug: str) -> bool:
        """Whether another change can start on the project now: fewer under way than it allows
        (parallel_jobs), and none of them a new project, an adoption or a job from before trees,
        which only run alone."""
        project, jobs = self.project(slug), self.change_jobs(slug)
        if project is None or len(jobs) >= max(project.parallel_jobs, 1):
            return False
        return not jobs or not any(j.kind in ("new", "adopt") or not j.worktree for j in jobs)

    def active_job(self, slug: str, questions: bool = True) -> Job | None:
        """The project's oldest unfinished job, if any. A question or a look for proposals can
        come in between its changes (`questions=False` leaves them out); a project allows one
        change at a time unless it says more (room_for_change)."""
        marks = ", ".join("?" * len(ACTIVE))
        skip = "" if questions else f" AND start NOT IN ({', '.join('?' * len(LOOKS))})"
        row = self.db.execute(
            f"SELECT * FROM jobs WHERE slug = ? AND state IN ({marks}){skip} ORDER BY id LIMIT 1",
            (slug, *ACTIVE, *(() if questions else LOOKS)),
        ).fetchone()
        return Job(**row) if row else None

    def jobs(self, states: tuple[str, ...] | None = None, limit: int = 50) -> list[Job]:
        if states:
            marks = ", ".join("?" * len(states))
            rows = self.db.execute(
                f"SELECT * FROM jobs WHERE state IN ({marks}) ORDER BY priority DESC, id LIMIT ?", (*states, limit)
            )
        else:  # the latest first, by when they began: a job brought back from GitHub has a new number but its old date
            rows = self.db.execute("SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))
        return [Job(**r) for r in rows]

    def project_jobs(self, slug: str, limit: int = 1000) -> list[Job]:
        """A project's jobs, the latest first (by when they began, as `jobs`)."""
        rows = self.db.execute("SELECT * FROM jobs WHERE slug = ? ORDER BY created_at DESC, id DESC LIMIT ?", (slug, limit))
        return [Job(**r) for r in rows]

    def job_list(self, slug: str) -> list[Job]:
        """Every one of a project's jobs, the latest first (as `jobs`), without their graphs and
        vars (LISTED): to list and search them all."""
        rows = self.db.execute(f"SELECT {LISTED} FROM jobs WHERE slug = ? ORDER BY created_at DESC, id DESC", (slug,))
        return [Job(**r) for r in rows]

    def project_job_count(self, slug: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM jobs WHERE slug = ?", (slug,)).fetchone()[0]

    def has_history(self, slug: str) -> bool:
        """Whether anything was said in a project's conversation before jobs had their own activity."""
        return self.db.execute("SELECT 1 FROM messages WHERE channel = ? AND job_id IS NULL LIMIT 1", (slug,)).fetchone() is not None

    def active_job_ids(self) -> set[int]:
        """Every unfinished job's id."""
        marks = ", ".join("?" * len(ACTIVE))
        return {r["id"] for r in self.db.execute(f"SELECT id FROM jobs WHERE state IN ({marks})", ACTIVE)}

    def ready_jobs(self, at: str | None = None, limit: int = 100) -> list[Job]:
        """Queued jobs whose project isn't paused, highest priority first; parked jobs come back
        once their time is up."""
        at = at or now()
        self.db.execute("UPDATE jobs SET state = 'queued', retry_at = NULL WHERE state = 'parked' AND retry_at <= ?", (at,))
        rows = self.db.execute(
            "SELECT jobs.* FROM jobs JOIN projects USING (slug)"
            " WHERE jobs.state = 'queued' AND projects.paused = 0"
            " ORDER BY jobs.priority DESC, jobs.id LIMIT ?",
            (limit,),
        )
        return [Job(**r) for r in rows]

    def next_job(self, at: str | None = None) -> Job | None:
        """The first of the ready jobs."""
        return next(iter(self.ready_jobs(at, limit=1)), None)

    def requeue_interrupted(self) -> list[Job]:
        """After a restart, a job left `running` carries on from its current step."""
        jobs = self.jobs(("running",))
        self.db.execute("UPDATE jobs SET state = 'queued' WHERE state = 'running'")
        return jobs

    # --- notes ----------------------------------------------------------------

    def add_note(self, job_id: int, text: str, step_id: int | None = None, node_id: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO notes (job_id, text, created_at, step_id, node_id) VALUES (?, ?, ?, ?, ?)", (job_id, text, now(), step_id, node_id)
        )
        return cur.lastrowid

    def note(self, note_id: int) -> tuple[int, str, bool] | None:
        """A note's job, text and whether it's been passed on to a step."""
        row = self.db.execute("SELECT job_id, text, consumed FROM notes WHERE id = ?", (note_id,)).fetchone()
        return (row["job_id"], row["text"], bool(row["consumed"])) if row else None

    def drop_note(self, note_id: int) -> None:
        self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    def pending_notes(self, job_id: int, step_id: int | None = None, node_id: str = "") -> list[tuple[int, str]]:
        """Notes not yet passed on: those for whichever step is next, and those for `step_id` (or
        for its block of the job's workflow, `node_id`)."""
        rows = self.db.execute(
            "SELECT id, text FROM notes WHERE job_id = ? AND consumed = 0 AND (step_id IS NULL OR step_id = ?)"
            " AND (node_id = '' OR node_id = ?) ORDER BY id",
            (job_id, step_id, node_id or "\0"),
        )
        return [(r["id"], r["text"]) for r in rows]

    def node_notes(self, job_id: int, node_id: str) -> list[str]:
        rows = self.db.execute("SELECT text FROM notes WHERE job_id = ? AND node_id = ? ORDER BY id", (job_id, node_id))
        return [r["text"] for r in rows]

    def step_notes(self, step_id: int) -> list[str]:
        rows = self.db.execute("SELECT text FROM notes WHERE step_id = ? ORDER BY id", (step_id,))
        return [r["text"] for r in rows]

    def consume_notes(self, ids: list[int]) -> None:
        self.db.executemany("UPDATE notes SET consumed = 1 WHERE id = ?", [(i,) for i in ids])

    def aim_notes_at(self, job_id: int, ids: list[int], node_id: str) -> None:
        """Pass notes on to one block of the job's workflow rather than whichever step comes next:
        the others don't see them."""
        self.db.executemany("UPDATE notes SET node_id = ? WHERE id = ? AND job_id = ?", [(node_id, i, job_id) for i in ids])

    # --- steps ----------------------------------------------------------------

    def add_steps(self, job_id: int, steps: list[Step], after: int | None = None) -> None:
        """Append steps to the job, or insert them after the step at position `after`."""
        with self.db:
            self.db.execute("BEGIN")
            if after is None:
                row = self.db.execute("SELECT MAX(position) AS last FROM steps WHERE job_id = ?", (job_id,)).fetchone()
                after = row["last"] or 0
            else:
                self.db.execute(
                    "UPDATE steps SET position = position + ? WHERE job_id = ? AND position > ?", (len(steps), job_id, after)
                )
            for i, s in enumerate(steps, start=after + 1):
                self.db.execute(
                    "INSERT INTO steps (job_id, position, kind, name, max_turns, optional, state, summary, problem, links, turn,"
                    " node_id, port, visit, model, effort, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, i, s.kind, s.name, s.max_turns, int(s.optional), s.state, s.summary, s.problem, json.dumps(s.links), s.turn,
                     s.node_id, s.port, s.visit, s.model, s.effort, s.started_at, s.ended_at),
                )

    def drop_steps_after(self, job_id: int, position: int) -> None:
        """Forget the steps after one (written by walking on from it, none started): undoing a skip."""
        self.db.execute("DELETE FROM steps WHERE job_id = ? AND position > ? AND started_at IS NULL", (job_id, position))

    def steps(self, job_id: int) -> list[Step]:
        rows = self.db.execute("SELECT * FROM steps WHERE job_id = ? ORDER BY position", (job_id,))
        return [_step(r) for r in rows]

    def step(self, step_id: int) -> Step | None:
        row = self.db.execute("SELECT * FROM steps WHERE id = ?", (step_id,)).fetchone()
        return _step(row) if row else None

    def update_step(self, step_id: int, **fields) -> Step:
        for name in ("links", "options", "design"):
            if name in fields:
                fields[name] = json.dumps(fields[name])
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE steps SET {columns} WHERE id = ?", (*fields.values(), step_id))
        return self.step(step_id)

    def advance(self, job_id: int, close: int | None, closed: dict, rows: list[Step], vars: dict, **job: object) -> list[Step]:
        """A step's end and what comes after it, in one go: the step `close` updated with `closed`
        (its state, the output it left by…), `rows` added after the job's last step (blocks
        passed on the way, the next to do), the job's vars and any other `job` fields saved."""
        for name in ("links", "options", "design"):
            if name in closed:
                closed[name] = json.dumps(closed[name])
        with self.db:
            self.db.execute("BEGIN")
            if close is not None and closed:
                columns = ", ".join(f"{name} = ?" for name in closed)
                self.db.execute(f"UPDATE steps SET {columns} WHERE id = ?", (*closed.values(), close))
            last = self.db.execute("SELECT MAX(position) AS last FROM steps WHERE job_id = ?", (job_id,)).fetchone()["last"] or 0
            for i, s in enumerate(rows, start=last + 1):
                self.db.execute(
                    "INSERT INTO steps (job_id, position, kind, name, max_turns, optional, state, summary, problem, links, turn,"
                    " node_id, port, visit, model, effort, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, i, s.kind, s.name, s.max_turns, int(s.optional), s.state, s.summary, s.problem, json.dumps(s.links),
                     s.turn, s.node_id, s.port, s.visit, s.model, s.effort, s.started_at, s.ended_at),
                )  # fmt: skip
            fields = {"vars": json.dumps(vars), **job, "updated_at": now()}
            columns = ", ".join(f"{name} = ?" for name in fields)
            self.db.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))
        return self.steps(job_id)

    # --- shipments ------------------------------------------------------------

    def add_shipment(self, slug: str, carrier: int, jobs: dict[int, int]) -> Shipment:
        """Changes shipping together: `jobs` maps each (the carrier first) to the step its run of ship steps began at."""
        cur = self.db.execute(
            "INSERT INTO shipments (slug, carrier, jobs, created_at) VALUES (?, ?, ?, ?)",
            (slug, carrier, json.dumps([[j, s] for j, s in jobs.items()]), now()),
        )
        return self.shipment(cur.lastrowid)

    def shipment(self, shipment_id: int) -> Shipment | None:
        row = self.db.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
        return _shipment(row) if row else None

    def shipping(self, slug: str | None = None) -> list[Shipment]:
        """Shipments under way, oldest first: the project's (one at most: it holds the turn to ship), or every project's."""
        where, args = ("AND slug = ?", (slug,)) if slug else ("", ())
        rows = self.db.execute(f"SELECT * FROM shipments WHERE state = 'shipping' {where} ORDER BY id", args)
        return [_shipment(r) for r in rows]

    def shipments_of(self, job_id: int) -> list[Shipment]:
        """The shipments a job went out in, or is going out in, oldest first."""
        rows = self.db.execute(
            "SELECT DISTINCT shipments.* FROM shipments, json_each(shipments.jobs) WHERE json_extract(json_each.value, '$[0]') = ?"
            " ORDER BY shipments.id",
            (job_id,),
        )
        return [_shipment(r) for r in rows]

    def end_shipment(self, shipment_id: int, state: str) -> Shipment:
        assert state in ("shipped", "dropped"), state
        self.db.execute("UPDATE shipments SET state = ?, ended_at = ? WHERE id = ?", (state, now(), shipment_id))
        return self.shipment(shipment_id)

    # --- the stage blocks' texts (stages.py) -----------------------------------

    def stage_texts(self) -> dict[tuple[str, str], dict]:
        rows = self.db.execute("SELECT * FROM stage_texts").fetchall()
        return {(r["stage"], r["part"]): {**dict(r), "edited": bool(r["edited"])} for r in rows}

    def put_stage_text(self, stage: str, part: str, text: str, seeded: str, edited: bool, upstream: str, sig: str) -> None:
        self.db.execute(
            "INSERT INTO stage_texts (stage, part, text, seeded, edited, upstream, sig, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(stage, part) DO UPDATE SET text = excluded.text, seeded = excluded.seeded, edited = excluded.edited,"
            " upstream = excluded.upstream, sig = excluded.sig, updated_at = excluded.updated_at",
            (stage, part, text, seeded, int(edited), upstream, sig, now()),
        )

    def drop_stage_text(self, stage: str, part: str) -> None:
        self.db.execute("DELETE FROM stage_texts WHERE stage = ? AND part = ?", (stage, part))

    # --- defaults -------------------------------------------------------------

    def defaults(self) -> dict[str, tuple[str, str]]:
        """{step name: (model, effort)}, "" where not set."""
        return {r["name"]: (r["model"], r["effort"]) for r in self.db.execute("SELECT * FROM defaults")}

    def set_default(self, name: str, field: str, value: str) -> None:
        assert field in ("model", "effort"), field
        self.db.execute(
            f"INSERT INTO defaults (name, {field}) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET {field} = excluded.{field}",
            (name, value),
        )

    def suggest_defaults(self, key: str, suggested: dict[str, tuple[str, str]]) -> None:
        """Golem's suggested defaults: the first time (per `key`) they fill what Riggy hasn't
        set; after that, only kinds of step new since (with no row yet). What he changes or
        unsets stays that way."""
        first, have = self.get(key) is None, set(self.defaults())
        for name, (model, effort) in suggested.items():
            if not first and name in have:
                continue
            self.db.execute(
                "INSERT INTO defaults (name, model, effort) VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "model = CASE WHEN model = '' THEN excluded.model ELSE model END, "
                "effort = CASE WHEN effort = '' THEN excluded.effort ELSE effort END",
                (name, model, effort),
            )
        self.put(key, "1")

    def reset_defaults(self, suggested: dict[str, tuple[str, str]]) -> None:
        """Every kind of step back to golem's suggestion, and "every step" unset."""
        self.db.execute("DELETE FROM defaults")
        self.db.executemany("INSERT INTO defaults (name, model, effort) VALUES (?, ?, ?)", [(n, *me) for n, me in suggested.items()])

    # --- proposals ------------------------------------------------------------

    def add_proposals(self, slug: str, job_id: int, lens: str, made: list[tuple[str, str] | tuple[str, str, dict]]) -> list[Proposal]:
        """A proposals job's (title, body, card if it has one), in place of any it made before (its step ran again)."""
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("DELETE FROM proposals WHERE job_id = ? AND state = 'open'", (job_id,))
            self.db.executemany(
                "INSERT INTO proposals (slug, job_id, lens, title, body, card, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(slug, job_id, lens, title, body, json.dumps(card[0] if card else {}), now()) for title, body, *card in made],
            )
        return self.job_proposals(job_id)

    def proposal(self, proposal_id: int) -> Proposal | None:
        row = self.db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return Proposal(**row) if row else None

    def proposals(self, slug: str, states: tuple[str, ...] | None = None, limit: int = 200) -> list[Proposal]:
        """A project's proposals, newest first; only those in `states` if given."""
        states = states or ("open", "accepted", "rejected")  # not those he dismissed: as if never proposed
        marks = ", ".join("?" * len(states))
        rows = self.db.execute(
            f"SELECT * FROM proposals WHERE slug = ? AND state IN ({marks}) ORDER BY id DESC LIMIT ?", (slug, *states, limit)
        )
        return [Proposal(**r) for r in rows]

    def proposals_waiting(self, slug: str, at: str) -> int:
        """How many of the project's proposals were awaiting Riggy at `at` (an ISO time): made by
        then and not decided yet."""
        row = self.db.execute(
            "SELECT COUNT(*) FROM proposals WHERE slug = ? AND created_at <= ? AND (decided_at IS NULL OR decided_at > ?)",
            (slug, at, at),
        ).fetchone()
        return row[0]

    def job_proposals(self, job_id: int) -> list[Proposal]:
        """What a proposals job proposed, in its order."""
        return [Proposal(**r) for r in self.db.execute("SELECT * FROM proposals WHERE job_id = ? ORDER BY id", (job_id,))]

    def decide_proposal(self, proposal_id: int, accepted: bool, reason: str) -> Proposal:
        self.db.execute(
            "UPDATE proposals SET state = ?, reason = ?, decided_at = ? WHERE id = ?",
            ("accepted" if accepted else "rejected", reason, now(), proposal_id),
        )
        return self.proposal(proposal_id)

    def dismiss_proposal(self, proposal_id: int) -> Proposal:
        """Riggy cleared it away without a verdict. Kept as a record, but listed only when asked for by state."""
        self.db.execute("UPDATE proposals SET state = 'dismissed', reason = '', decided_at = ? WHERE id = ?", (now(), proposal_id))
        return self.proposal(proposal_id)

    def unbuilt_proposals(self) -> list[Proposal]:
        """Accepted proposals with no job yet (their project was busy), oldest first. Not golem's
        for itself (INTERNAL): Riggy makes those."""
        rows = self.db.execute(
            "SELECT * FROM proposals WHERE state = 'accepted' AND built_by IS NULL AND slug != ? ORDER BY decided_at, id", (INTERNAL,)
        )
        return [Proposal(**r) for r in rows]

    def set_built_by(self, proposal_id: int, job_id: int) -> None:
        self.db.execute("UPDATE proposals SET built_by = ? WHERE id = ?", (job_id, proposal_id))

    def set_proposal_issue(self, proposal_id: int, issue: int | None) -> None:
        self.db.execute("UPDATE proposals SET issue = ? WHERE id = ?", (issue, proposal_id))

    def proposal_jobs(self, slug: str) -> list[Job]:
        """The project's proposals jobs, oldest first."""
        rows = self.db.execute("SELECT * FROM jobs WHERE slug = ? AND start = 'propose' ORDER BY id", (slug,))
        return [Job(**r) for r in rows]

    def jobs_from(self, start: str, limit: int = 1000) -> list[Job]:
        """Jobs that came from `start` (STARTS, TRIAGE), the latest `limit`, oldest first."""
        rows = self.db.execute(f"SELECT {LISTED} FROM jobs WHERE start = ? ORDER BY id DESC LIMIT ?", (start, limit))
        return [Job(**r) for r in rows][::-1]

    def add_triaged(self, job_id: int, upto: int, made: list[tuple[str, str, str, list[int]]], notes: dict[int, str]) -> list[Proposal]:
        """What a triage job (triage.py) made of the new reports up to #`upto`: its proposals for
        golem, (title, body, area, the reports each cites), in place of any it made before (its
        step ran again); each report it read triaged, with the proposal citing it or its note."""
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("DELETE FROM proposals WHERE job_id = ? AND state = 'open'", (job_id,))
            for title, body, area, cited in made:
                cur = self.db.execute(
                    "INSERT INTO proposals (slug, job_id, lens, title, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (INTERNAL, job_id, area, title, body, now()),
                )
                self.db.executemany(
                    "UPDATE reports SET proposal_id = ? WHERE id = ? AND id <= ? AND state = 'new'",
                    [(cur.lastrowid, i, upto) for i in cited],
                )
            self.db.executemany(
                "UPDATE reports SET note = ? WHERE id = ? AND id <= ? AND state = 'new' AND proposal_id IS NULL",
                [(why, i, upto) for i, why in notes.items()],
            )
            self.db.execute("UPDATE reports SET state = 'triaged', triage_job = ? WHERE state = 'new' AND id <= ?", (job_id, upto))
        return self.job_proposals(job_id)

    # --- reports about golem itself (reports.py) ---------------------------------

    def _insert_report(self, source: str, kind: str, impact: str, summary: str, detail: str, fingerprint: str, place: dict) -> int:
        at = now()
        extra = {k: v for k, v in place.items() if k in REPORT_PLACE and v is not None}
        columns = ["source", "kind", "impact", "summary", "detail", "fingerprint", "first_seen", "last_seen", *extra]
        cur = self.db.execute(
            f"INSERT INTO reports ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            (source, kind, impact, summary, detail, fingerprint, at, at, *extra.values()),
        )
        return cur.lastrowid

    def add_report(self, kind: str, impact: str, summary: str, detail: str = "", **place) -> Report:
        """A worker's report. `place` is where it came from (REPORT_PLACE)."""
        return self.report(self._insert_report("agent", kind, impact, summary, detail, "", place))

    def file_incident(self, fingerprint: str, kind: str, impact: str, summary: str, detail: str = "", **place) -> Report:
        """One of golem's own incidents: counted on its report if it's had one, the latest of where
        and what it said kept, at its worst impact; a new report otherwise. Once Riggy took a fix
        for one (accepted), it happening again is news: a new report."""
        with self.db:
            self.db.execute("BEGIN")
            row = self.db.execute(
                "SELECT * FROM reports WHERE fingerprint = ? AND state != 'accepted' ORDER BY id DESC LIMIT 1", (fingerprint,)
            ).fetchone()
            if row is None:
                found = self._insert_report("golem", kind, impact, summary, detail, fingerprint, place)
            else:
                found = row["id"]
                latest = {k: place.get(k) for k in REPORT_PLACE if k in place}
                fields = {"count": row["count"] + 1, "last_seen": now(), "impact": worst(row["impact"], impact),
                          "summary": summary, "detail": detail, **latest}  # fmt: skip
                self.db.execute(
                    f"UPDATE reports SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), found)
                )
        return self.report(found)

    def report(self, report_id: int) -> Report | None:
        row = self.db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return Report(**row) if row else None

    def reports(self, states: tuple[str, ...] | None = None, source: str = "", limit: int = 200) -> list[Report]:
        """Reports, the latest to happen first; only those in `states`, and from `source`, if given."""
        where, args = [], []
        if states:
            where.append(f"state IN ({', '.join('?' * len(states))})")
            args += states
        if source:
            where.append("source = ?")
            args.append(source)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = self.db.execute(f"SELECT * FROM reports{clause} ORDER BY last_seen DESC, id DESC LIMIT ?", (*args, limit))
        return [Report(**r) for r in rows]

    def proposal_reports(self, proposal_id: int) -> list[Report]:
        """The reports a proposal for golem cites, oldest first."""
        return [Report(**r) for r in self.db.execute("SELECT * FROM reports WHERE proposal_id = ? ORDER BY id", (proposal_id,))]

    def decide_reports(self, proposal_id: int, state: str) -> None:
        """The reports a proposal for golem cites, accepted with it or dismissed."""
        assert state in ("accepted", "dismissed"), state
        self.db.execute("UPDATE reports SET state = ? WHERE proposal_id = ?", (state, proposal_id))

    def new_reports(self, limit: int = 100) -> list[Report]:
        """Reports triage hasn't read yet, oldest first."""
        rows = self.db.execute("SELECT * FROM reports WHERE state = 'new' ORDER BY id LIMIT ?", (limit,))
        return [Report(**r) for r in rows]

    # --- runs -----------------------------------------------------------------

    def start_run(self, job_id: int, phase: str, model: str, log_path: str, step_id: int | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO runs (job_id, phase, model, started_at, log_path, step_id) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, phase, model, now(), log_path, step_id),
        )
        return cur.lastrowid

    def finish_run(self, run_id: int, outcome: str, **usage) -> None:
        fields = {"outcome": outcome, "ended_at": now(), **usage}
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE runs SET {columns} WHERE id = ?", (*fields.values(), run_id))

    def run(self, run_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def finished_runs(self, since: str = "") -> list[dict]:
        """Runs that ended, started at or after `since`, with their project and step."""
        rows = self.db.execute(
            "SELECT runs.*, jobs.slug, steps.name AS step_name FROM runs JOIN jobs ON jobs.id = runs.job_id"
            " LEFT JOIN steps ON steps.id = runs.step_id WHERE runs.ended_at IS NOT NULL AND runs.started_at >= ?"
            " ORDER BY runs.id",
            (since,),
        )
        return [dict(r) for r in rows]

    def finished_steps(self, since: str = "") -> list[tuple[Step, str]]:
        """Steps that ran and ended at or after `since`, with their project's slug."""
        rows = self.db.execute(
            "SELECT steps.*, jobs.slug FROM steps JOIN jobs ON jobs.id = steps.job_id"
            " WHERE steps.started_at IS NOT NULL AND steps.ended_at >= ? ORDER BY steps.ended_at",
            (since,),
        )
        found = []
        for r in rows:
            data = dict(r)
            slug = data.pop("slug")
            found.append((_step(data), slug))
        return found

    def runs(self, job_id: int | None = None, limit: int = 50) -> list[dict]:
        if job_id is None:
            rows = self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = self.db.execute("SELECT * FROM runs WHERE job_id = ? ORDER BY id", (job_id,))
        return [dict(r) for r in rows]


def start_of(kind: str, playbook: str) -> str:
    """Where a job came from, by its kind and playbook (STARTS)."""
    if kind in ("new", "adopt"):
        return kind
    return {"question": "look", "proposals": "propose"}.get(playbook, "change")


def _step(row: sqlite3.Row | dict) -> Step:
    data = dict(row)
    data["links"] = json.loads(data["links"] or "{}")
    data["options"] = json.loads(data.get("options") or "[]")
    data["design"] = json.loads(data.get("design") or "{}")
    data.pop("item", None)  # unused: a checklist item's (checklist mode, gone)
    data["optional"] = bool(data["optional"])
    return Step(**data)


def _shipment(row: sqlite3.Row) -> Shipment:
    data = dict(row)
    data["jobs"] = {int(j): int(s) for j, s in json.loads(data["jobs"])}
    return Shipment(**data)


def _project(row: sqlite3.Row | dict) -> Project:
    # Only Project's fields: older databases have a column golem no longer uses.
    data = {f.name: row[f.name] for f in fields(Project)}
    data["paused"] = bool(data["paused"])
    data["vacuum"] = bool(data["vacuum"])
    return Project(**data)
