"""SQLite: the project registry, the job queue and each job's steps, Riggy's notes to jobs,
run records, and the conversation (talk.py).

One process owns the database and every call is short, so a single connection is enough.
"""

from __future__ import annotations

import sqlite3
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'standard',
    model TEXT NOT NULL DEFAULT 'opus',
    effort TEXT NOT NULL DEFAULT 'high',
    paused INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    tag TEXT NOT NULL DEFAULT '',    -- what sort of project it is (tags), '' for none
    proposals TEXT NOT NULL DEFAULT 'off',  -- how often golem looks for improvements to propose (EVERY)
    proposals_max INTEGER NOT NULL DEFAULT 5,  -- proposals awaiting Riggy before golem stops making more
    proposals_off TEXT NOT NULL DEFAULT ''  -- kinds of proposal (proposals.LENSES) he turned off, comma-separated
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
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    text TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
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
-- A job's steps, in order, written from its playbook when it starts. golem works
-- through them one at a time; a step that fails gets a fix and a retry inserted after it.
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- agent, or one of golem's own (playbook.CODE_STEPS)
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
    options TEXT NOT NULL DEFAULT '[]'  -- JSON: an options step's options (options.py)
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
    at TEXT NOT NULL
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
    body TEXT NOT NULL,              -- what, why, and roughly how big
    state TEXT NOT NULL DEFAULT 'open',  -- open | accepted | rejected
    reason TEXT NOT NULL DEFAULT '', -- Riggy's why, if he gave one
    built_by INTEGER,                -- the change job an accepted one became, once it's queued
    created_at TEXT NOT NULL,
    decided_at TEXT,
    issue INTEGER                    -- its GitHub issue: a record, never read back
);
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
    },
    "proposals": {"issue": "INTEGER"},
    "runs": {"step_id": "INTEGER"},
    "steps": {
        "optional": "INTEGER NOT NULL DEFAULT 0",
        "model": "TEXT",
        "effort": "TEXT",
        "options": "TEXT NOT NULL DEFAULT '[]'",
    },
    "notes": {"step_id": "INTEGER"},  # a note for one step rather than the next
}
# Columns golem no longer uses, dropped from older databases on open.
DROPPED = {"messages": ("via",), "cards": ("tags",)}
GAME = "Game"  # the one tag golem starts with
TAG_COLOR = 7

# Jobs in these states hold or want the worker slot, or wait on Riggy; anything else is finished.
ACTIVE = ("queued", "running", "waiting", "parked")
# Steps in these states are still to do: the first of them is the job's current step.
OPEN = ("pending", "running", "waiting")
MODES = ("reckless", "standard", "rigorous")
# Playbooks whose jobs only look: nothing is committed, pushed or deployed, so they can run
# between the steps of a project's change.
LOOKS = ("question", "proposals")
EVERY = ("off", "hourly", "daily", "weekly")  # how often a project gets proposals
MODELS = ("fable", "opus", "sonnet", "haiku")
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


@dataclass
class Proposal:
    """An improvement golem proposed for a project, and what Riggy made of it."""

    id: int
    slug: str
    job_id: int
    lens: str
    title: str
    body: str
    state: str  # open | accepted | rejected
    reason: str
    built_by: int | None  # the change job building it, once accepted and queued
    created_at: str
    decided_at: str | None
    issue: int | None = None  # its GitHub issue, a record of it there: golem never reads it back


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


class Store:
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
        if self.get("project_engines") is None:
            # Projects used to always name a model and effort, opus and high unless Riggy said
            # otherwise. Those now come from each step's defaults, which start out the same.
            self.db.execute("UPDATE projects SET model = '' WHERE model = 'opus'")
            self.db.execute("UPDATE projects SET effort = '' WHERE effort = 'high'")
            self.put("project_engines", "1")

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
        self, slug: str, name: str, summary: str, mode: str = "standard", model: str = "", effort: str = ""
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
        return [_project(r) for r in self.db.execute("SELECT * FROM projects ORDER BY created_at DESC")]

    def set_paused(self, slug: str, paused: bool) -> None:
        self.db.execute("UPDATE projects SET paused = ? WHERE slug = ?", (int(paused), slug))

    def update_project(self, slug: str, **fields: str | int) -> Project:
        assert set(fields) <= {"name", "mode", "model", "effort", "tag", "proposals", "proposals_max", "proposals_off"}, fields
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE projects SET {columns} WHERE slug = ?", (*fields.values(), slug))
        return self.project(slug)

    def move_project(self, old: str, new: str) -> Project:
        """Give a project a new slug, keeping its jobs."""
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute(
                "INSERT INTO projects (slug, name, summary, mode, model, effort, paused, created_at, tag, proposals, proposals_max, proposals_off)"
                " SELECT ?, name, summary, mode, model, effort, paused, created_at, tag, proposals, proposals_max, proposals_off"
                " FROM projects WHERE slug = ?",
                (new, old),
            )
            self.db.execute("UPDATE jobs SET slug = ? WHERE slug = ?", (new, old))
            self.db.execute("UPDATE proposals SET slug = ? WHERE slug = ?", (new, old))
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
            self.db.execute("DELETE FROM jobs WHERE slug = ?", (slug,))
            self.db.execute("DELETE FROM cards WHERE slug = ?", (slug,))
            self.db.execute("DELETE FROM messages WHERE channel = ?", (slug,))
            self.db.execute("DELETE FROM projects WHERE slug = ?", (slug,))
        return ids

    def restore(self, project: Project, jobs: list[tuple[Job, list[Step]]]) -> None:
        """Put back a project and its finished jobs as GitHub remembers them (recover.py), with
        their ids: new jobs then come after them, and never reuse an old job's branch."""
        with self.db:
            self.db.execute("BEGIN")
            row = asdict(project)
            self.db.execute(f"INSERT INTO projects ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
            for job, steps in jobs:
                row = asdict(job)
                self.db.execute(f"INSERT INTO jobs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
                self.db.executemany(
                    "INSERT INTO steps (job_id, position, kind, name, state, summary, links) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(job.id, i, s.kind, s.name, s.state, s.summary, json.dumps(s.links)) for i, s in enumerate(steps, start=1)],
                )

    def live_projects(self) -> list[tuple[Project, str]]:
        """Projects with a site: deployed by a step, or with a finished job (from before steps
        were tracked). With when they last went live, latest first."""
        rows = self.db.execute(
            "SELECT projects.*, MAX(COALESCE(steps.ended_at, jobs.updated_at)) AS live_at FROM projects"
            " JOIN jobs USING (slug) LEFT JOIN steps ON steps.job_id = jobs.id AND steps.kind = 'deploy' AND steps.state = 'done'"
            " WHERE steps.id IS NOT NULL OR jobs.state = 'done' GROUP BY projects.slug ORDER BY live_at DESC"
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
        self, channel: str, who: str, text: str, image: str | None = None, notify: bool = False
    ) -> Said:
        cur = self.db.execute(
            "INSERT INTO messages (channel, who, text, image, notify, at) VALUES (?, ?, ?, ?, ?, ?)",
            (channel, who, text, image, int(notify), now()),
        )
        return self.messages(channel, after=cur.lastrowid - 1, limit=1)[0]

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

    def add_job(self, slug: str, kind: str, request: str, playbook: str, priority: int = 0) -> Job:
        cur = self.db.execute(
            "INSERT INTO jobs (slug, kind, request, playbook, priority, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (slug, kind, request, playbook, priority, now(), now()),
        )
        return self.job(cur.lastrowid)

    def job(self, job_id: int) -> Job | None:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job(**row) if row else None

    def update_job(self, job_id: int, **fields) -> Job:
        fields["updated_at"] = now()
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))
        return self.job(job_id)

    def active_job(self, slug: str, questions: bool = True) -> Job | None:
        """The project's unfinished job, if any. A project works on one change at a time; a
        question or a look for proposals can come in between (`questions=False` leaves them out)."""
        marks = ", ".join("?" * len(ACTIVE))
        skip = "" if questions else f" AND playbook NOT IN ({', '.join('?' * len(LOOKS))})"
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
        else:
            rows = self.db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        return [Job(**r) for r in rows]

    def next_job(self, at: str | None = None) -> Job | None:
        """Highest priority queued job whose project isn't paused; parked jobs come back once their time is up."""
        at = at or now()
        self.db.execute("UPDATE jobs SET state = 'queued', retry_at = NULL WHERE state = 'parked' AND retry_at <= ?", (at,))
        row = self.db.execute(
            "SELECT jobs.* FROM jobs JOIN projects USING (slug)"
            " WHERE jobs.state = 'queued' AND projects.paused = 0"
            " ORDER BY jobs.priority DESC, jobs.id LIMIT 1"
        ).fetchone()
        return Job(**row) if row else None

    def requeue_interrupted(self) -> list[Job]:
        """After a restart, a job left `running` carries on from its current step."""
        jobs = self.jobs(("running",))
        self.db.execute("UPDATE jobs SET state = 'queued' WHERE state = 'running'")
        return jobs

    # --- notes ----------------------------------------------------------------

    def add_note(self, job_id: int, text: str, step_id: int | None = None) -> None:
        self.db.execute(
            "INSERT INTO notes (job_id, text, created_at, step_id) VALUES (?, ?, ?, ?)", (job_id, text, now(), step_id)
        )

    def pending_notes(self, job_id: int, step_id: int | None = None) -> list[tuple[int, str]]:
        """Notes not yet passed on: those for whichever step is next, and those for `step_id`."""
        rows = self.db.execute(
            "SELECT id, text FROM notes WHERE job_id = ? AND consumed = 0 AND (step_id IS NULL OR step_id = ?) ORDER BY id",
            (job_id, step_id),
        )
        return [(r["id"], r["text"]) for r in rows]

    def step_notes(self, step_id: int) -> list[str]:
        rows = self.db.execute("SELECT text FROM notes WHERE step_id = ? ORDER BY id", (step_id,))
        return [r["text"] for r in rows]

    def consume_notes(self, ids: list[int]) -> None:
        self.db.executemany("UPDATE notes SET consumed = 1 WHERE id = ?", [(i,) for i in ids])

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
                    "INSERT INTO steps (job_id, position, kind, name, max_turns, optional, state, summary, problem, links)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, i, s.kind, s.name, s.max_turns, int(s.optional), s.state, s.summary, s.problem, json.dumps(s.links)),
                )

    def steps(self, job_id: int) -> list[Step]:
        rows = self.db.execute("SELECT * FROM steps WHERE job_id = ? ORDER BY position", (job_id,))
        return [_step(r) for r in rows]

    def step(self, step_id: int) -> Step | None:
        row = self.db.execute("SELECT * FROM steps WHERE id = ?", (step_id,)).fetchone()
        return _step(row) if row else None

    def update_step(self, step_id: int, **fields) -> Step:
        for name in ("links", "options"):
            if name in fields:
                fields[name] = json.dumps(fields[name])
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE steps SET {columns} WHERE id = ?", (*fields.values(), step_id))
        return self.step(step_id)

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

    def add_proposals(self, slug: str, job_id: int, lens: str, made: list[tuple[str, str]]) -> list[Proposal]:
        """A proposals job's (title, body) pairs, in place of any it made before (its step ran again)."""
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("DELETE FROM proposals WHERE job_id = ? AND state = 'open'", (job_id,))
            self.db.executemany(
                "INSERT INTO proposals (slug, job_id, lens, title, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                [(slug, job_id, lens, title, body, now()) for title, body in made],
            )
        return self.job_proposals(job_id)

    def proposal(self, proposal_id: int) -> Proposal | None:
        row = self.db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return Proposal(**row) if row else None

    def proposals(self, slug: str, states: tuple[str, ...] | None = None, limit: int = 200) -> list[Proposal]:
        """A project's proposals, newest first; only those in `states` if given."""
        states = states or ("open", "accepted", "rejected")
        marks = ", ".join("?" * len(states))
        rows = self.db.execute(
            f"SELECT * FROM proposals WHERE slug = ? AND state IN ({marks}) ORDER BY id DESC LIMIT ?", (slug, *states, limit)
        )
        return [Proposal(**r) for r in rows]

    def job_proposals(self, job_id: int) -> list[Proposal]:
        """What a proposals job proposed, in its order."""
        return [Proposal(**r) for r in self.db.execute("SELECT * FROM proposals WHERE job_id = ? ORDER BY id", (job_id,))]

    def decide_proposal(self, proposal_id: int, accepted: bool, reason: str) -> Proposal:
        self.db.execute(
            "UPDATE proposals SET state = ?, reason = ?, decided_at = ? WHERE id = ?",
            ("accepted" if accepted else "rejected", reason, now(), proposal_id),
        )
        return self.proposal(proposal_id)

    def unbuilt_proposals(self) -> list[Proposal]:
        """Accepted proposals with no job yet (their project was busy), oldest first."""
        rows = self.db.execute("SELECT * FROM proposals WHERE state = 'accepted' AND built_by IS NULL ORDER BY decided_at, id")
        return [Proposal(**r) for r in rows]

    def set_built_by(self, proposal_id: int, job_id: int) -> None:
        self.db.execute("UPDATE proposals SET built_by = ? WHERE id = ?", (job_id, proposal_id))

    def set_proposal_issue(self, proposal_id: int, issue: int | None) -> None:
        self.db.execute("UPDATE proposals SET issue = ? WHERE id = ?", (issue, proposal_id))

    def proposal_jobs(self, slug: str) -> list[Job]:
        """The project's proposals jobs, oldest first."""
        rows = self.db.execute("SELECT * FROM jobs WHERE slug = ? AND playbook = 'proposals' ORDER BY id", (slug,))
        return [Job(**r) for r in rows]

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


def _step(row: sqlite3.Row | dict) -> Step:
    data = dict(row)
    data["links"] = json.loads(data["links"] or "{}")
    data["options"] = json.loads(data.get("options") or "[]")
    data["optional"] = bool(data["optional"])
    return Step(**data)


def _project(row: sqlite3.Row | dict) -> Project:
    # Only Project's fields: older databases have a column golem no longer uses.
    data = {f.name: row[f.name] for f in fields(Project)}
    data["paused"] = bool(data["paused"])
    return Project(**data)
