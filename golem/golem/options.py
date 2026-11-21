"""Options rounds: Riggy sees a few takes on something and picks one before it's built.

A message in a project's conversation that asks to see options ("I want a new art style, show me
three that are more chibi") starts a job on the options playbook instead of the feature
one; so does `!options`. Its `options` step mocks each option up for real (screenshots,
and the files behind them, in docs/options/ on the job's branch) and returns them with
its verdict. The `choose` step, golem's own, posts them in the project's conversation as A, B,
C… with their pictures and waits for his reply. His pick, with where its files are, goes
to the next step, which builds it. Asking for other options instead runs another round.

Whether a message wants options (prompts/request.md) and what a reply to them says
(prompts/pick.md) are cheap model calls; plain rules stand in when there's no model.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import plan
from .cards import DENY
from .config import Settings
from .harness import VERDICT_SCHEMA, Harness
from .store import Step

log = logging.getLogger(__name__)

LETTERS = "ABCDEF"
# Agent steps that make options for a `choose` step to show: their verdict brings them (SCHEMA).
MAKERS = ("options",)
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
IMAGES_SHOWN = 3  # an option's first few pictures are shown; the rest are just files
MAX_IMAGE_BYTES = 5 * 1024 * 1024
CALL_MODEL = "haiku"
# The options step's verdict: a step verdict, and when it's done, the options.
SCHEMA = {
    **VERDICT_SCHEMA,
    "properties": {
        **VERDICT_SCHEMA["properties"],
        "options": {
            "type": "array",
            "minItems": 2,
            "maxItems": len(LETTERS),
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The option in a few words"},
                    "description": {"type": "string", "description": "One or two plain sentences: what it is, why pick it"},
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Its files as paths from the repo root, the screenshots to show first",
                    },
                },
                "required": ["title", "description", "files"],
                "additionalProperties": False,
            },
        },
    },
}
# A design round (skills/design.md): its directions are the options, each with its own pages'
# pictures; and the doc they're all in, the pages they share, what else to decide, and a pick.
PAGES_SHOWN = 4  # pictures of the pages every direction shares (principles, logo sheet, decisions)
QUESTIONS = 8
MAX_DOC_BYTES = 10 * 1024 * 1024
DESIGN_SCHEMA = {
    **SCHEMA,
    "properties": {
        **SCHEMA["properties"],
        "doc": {"type": "string", "description": "The design doc: one self-contained HTML file, its path from the repo root"},
        "pages": {
            "type": "array",
            "maxItems": PAGES_SHOWN,
            "items": {"type": "string"},
            "description": "Pictures of the pages every direction shares, in order, as paths from the repo root",
        },
        "questions": {
            "type": "array",
            "maxItems": QUESTIONS,
            "items": {"type": "string"},
            "description": "What else there is to decide besides the direction, one question each, its choices by name, never by letter",
        },
        "recommendation": {"type": "string", "description": "Your pick in a sentence: the direction, and your answer to each question"},
        "skip": {"type": "string", "description": "Only if there's nothing to design: why, in a sentence, and no options"},
    },
}
REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["change", "options", "question"]},
        "title": {"type": "string", "description": "What it's about in 2 to 5 words, Title Case: 'Library View Redesign'"},
    },
    "required": ["kind", "title"],
    "additionalProperties": False,
}
REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"enum": ["pick", "more"]},
        "picked": {"type": "array", "items": {"enum": list(LETTERS)}},
    },
    "required": ["decision", "picked"],
    "additionalProperties": False,
}

# Without a model: a message that asks to see options, and replies that want other ones.
ASKS_RE = re.compile(
    r"\b(options?|alternatives?|variations?|mock ?ups?|choices)\b.*\b(pick|choose|decide|see|show|look)\b"
    r"|\b(pick|choose|decide|see|show|look)\b.*\b(options|alternatives|variations|mock ?ups|choices)\b"
    r"|\b(show|give) me (a few|some|two|three|four|five|\d)\b",
    re.I | re.S,
)
# Without a model: a question rather than a request phrased as one ("can you add…?").
QUESTION_RE = re.compile(
    r"^\s*(what|why|how|when|where|which|who|whose|is|are|was|were|does|do|did|has|have|should)\b|\?\s*$", re.I
)
REQUEST_RE = re.compile(r"^\s*((can|could|would|will) (you|u)|how about|what about)\b|\b(please|make|add|change|fix)\b", re.I)
LETTER_RE = re.compile(r"^\W*(?:[Oo]ption\s+)?([a-fA-F])\W*$|^\s*(?:[Oo]ption\s+)?([A-F])[,.:;!)\-]")
MORE_RE = re.compile(
    r"\b(none of (these|them|those)|(other|more|different|new) (options|ones|takes|ideas)|try again|start over|something else)\b",
    re.I,
)


@dataclass
class Option:
    letter: str
    title: str
    description: str
    files: list[str]  # in the project's repo, from its root
    images: list[str]  # copies of its first pictures: file names in the job's options folder (folder())

    @property
    def where(self) -> str:
        """Its files in a few words: their folder, or the one file."""
        if len(self.files) == 1:
            return self.files[0]
        return f"{os.path.commonpath(self.files)}/" if self.files else ""


@dataclass
class Reply:
    more: bool  # he wants other options instead
    picked: list[str]  # the letters he chose, in his order; none leaves it to the worker


@dataclass
class Design:
    """What a design round brings besides its directions (the options)."""

    doc: str = ""  # the doc's copy: a file name in the job's options folder (folder())
    source: str = ""  # the doc in the repo, from its root
    pages: list[str] = field(default_factory=list)  # copies of the shared pages' pictures, in order
    questions: list[str] = field(default_factory=list)
    recommendation: str = ""
    skip: str = ""  # why there's nothing to design, when the step found nothing to


def folder(settings: Settings, job_id: int) -> Path:
    """Where copies of a job's option pictures are kept, for the conversation and the status page."""
    return settings.job_logs(job_id) / "options"


def collect(raw: object, checkout: Path, dest: Path, prefix: str) -> list[Option]:
    """The options a worker returned, lettered in order, with copies of their first pictures
    in `dest` (named `<prefix>-<letter>-<n>`). Only files inside the checkout count."""
    root = checkout.resolve()
    found: list[Option] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or len(found) == len(LETTERS):
            continue
        letter = LETTERS[len(found)]
        files, images = [], []
        for name in item.get("files") or []:
            path = _inside(root, name)
            if path is None:
                continue
            files.append(path.relative_to(root).as_posix())
            if len(images) < IMAGES_SHOWN:
                _copy_image(path, dest, f"{prefix}-{letter.lower()}-{len(images) + 1}", images)
        title = str(item.get("title") or "").strip()[:80] or f"Option {letter}"
        found.append(Option(letter, title, str(item.get("description") or "").strip()[:500], files[:30], images))
    return found


def collect_design(raw: dict, checkout: Path, dest: Path, prefix: str) -> tuple[Design, str]:
    """A design round's doc and shared pages (copied to `dest` as `<prefix>-doc.html` and
    `<prefix>-p-<n>`), its questions and its pick; and what's wrong with it, or "". The doc is
    served from golem's own site, so it has to be one file that runs nothing (web.DOC_POLICY)."""
    root = checkout.resolve()
    skip = " ".join(str(raw.get("skip") or "").split())[:300]
    pages: list[str] = []
    for name in raw.get("pages") or []:
        path = _inside(root, name)
        if path is not None and len(pages) < PAGES_SHOWN:
            _copy_image(path, dest, f"{prefix}-p-{len(pages) + 1}", pages)
    design = Design(
        pages=pages,
        questions=[q for q in (" ".join(str(x).split())[:300] for x in (raw.get("questions") or [])[:QUESTIONS]) if q],
        recommendation=" ".join(str(raw.get("recommendation") or "").split())[:500],
        skip=skip,
    )
    if skip:
        return design, ""
    named = str(raw.get("doc") or "").strip()
    path = _inside(root, named) if named else None
    if path is None:
        return design, "The design step returned no doc: one self-contained HTML file in the repo, its path in `doc`."
    if path.suffix.lower() not in (".html", ".htm"):
        return design, f"The design doc {named} isn't an HTML file."
    if path.stat().st_size > MAX_DOC_BYTES:
        return design, f"The design doc {named} is over {MAX_DOC_BYTES // 2**20} MB: subset its fonts and shrink its pictures."
    text = path.read_text(errors="replace").lower()
    if "<script" in text or "http-equiv" in text:
        return design, f"The design doc {named} has a <script> or http-equiv: it has to run nothing (skills/design.md)."
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest / f"{prefix}-doc.html")
    design.doc, design.source = f"{prefix}-doc.html", path.relative_to(root).as_posix()
    return design, ""


def _inside(root: Path, name: object) -> Path | None:
    """A file a worker named, if it's in the checkout (`root`, resolved) and not git's."""
    path = (root / str(name)).resolve()
    if not path.is_relative_to(root) or not path.is_file() or ".git" in path.relative_to(root).parts:
        return None
    return path


def _copy_image(path: Path, dest: Path, stem: str, into: list[str]) -> bool:
    """Copy a picture to `dest` as `stem` and its type, and add its name to `into`, if it's one golem shows."""
    suffix = path.suffix.lower()
    if suffix not in IMAGE_TYPES or path.stat().st_size > MAX_IMAGE_BYTES:
        return False
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest / f"{stem}{suffix}")
    into.append(f"{stem}{suffix}")
    return True


def makes(step: Step) -> bool:
    """Whether a step makes options for Riggy to pick from (MAKERS)."""
    return step.kind == "agent" and step.name in MAKERS


def shown(steps: list[Step], step: Step) -> tuple[Step | None, list[Option]]:
    """The step that made the options a step is about, and its options: itself, or for a
    `choose` step, the latest maker before it (none of its own if that round was skipped)."""
    if makes(step):
        return step, [Option(**o) for o in step.options]
    if step.kind != "choose":
        return None, []
    made = next((s for s in reversed(steps) if s.position < step.position and makes(s)), None)
    return made, [Option(**o) for o in made.options] if made else []


def is_image(name: str) -> bool:
    """A name `collect` could have given a copy, `<step>-<letter>-<n>.<image type>`, or
    `collect_design` a shared page's, `<step>-p-<n>.<image type>`."""
    stem, _, suffix = name.rpartition(".")
    return bool(re.fullmatch(r"\d+-[a-fp]-\d+", stem)) and f".{suffix}" in IMAGE_TYPES


def is_doc(name: str) -> bool:
    """A name `collect_design` could have given a design doc's copy: `<step>-doc.html`."""
    return bool(re.fullmatch(r"\d+-doc\.html", name))


def picked(reply: Reply, found: list[Option], said: str) -> str:
    """What the pick step says it got: in "Done so far" for the step that builds it."""
    chosen = [o for letter in reply.picked for o in found if o.letter == letter]
    quote = f" You said: “{_short(said)}”." if said else ""
    if not chosen:
        return f"You didn't pick one option, so the next step decides from what you said.{quote}"
    names = " and ".join(f"{o.letter} ({o.title})" for o in chosen)
    files = [f"{o.letter} in {o.where}" if len(chosen) > 1 else o.where for o in chosen if o.files]
    where = f" Files: {'; '.join(files)}." if files else ""
    return f"You picked {names}.{quote}{where}"


# --- reading Riggy --------------------------------------------------------------------


def asks_for_options(text: str) -> bool:
    """Without a model: whether a message asks to see options before anything's built."""
    return bool(ASKS_RE.search(text))


def plain_reply(text: str, letters: list[str]) -> Reply:
    """Without a model: a reply that's a letter ("B", "b, but bigger eyes") picks it; one that
    wants other options asks for more; anything else goes to the next step as it is."""
    match = LETTER_RE.match(text.strip())
    letter = (match.group(1) or match.group(2)).upper() if match else ""
    if letter in letters:
        return Reply(False, [letter])
    return Reply(bool(MORE_RE.search(text)), [])


def asks_a_question(text: str) -> bool:
    """Without a model: whether a message asks something rather than for something."""
    return bool(QUESTION_RE.search(text)) and not REQUEST_RE.search(text)


@dataclass
class Request:
    """What a message in a project's conversation asks for, and the job's title if it becomes one."""

    kind: str  # change | options | question
    title: str  # "" without a model: plan.title falls back to his words


async def read_request(harness: Harness, settings: Settings, text: str) -> Request:
    """What a message in a project's conversation asks for: a `change`, `options` to pick from first,
    or only an answer to a `question`. The same call names it, for the job it starts."""
    if settings.harness != "fake":
        v = await _ask(harness, settings, "request", {"$request": text.strip()}, REQUEST_SCHEMA)
        if v and v.get("kind") in ("change", "options", "question"):
            return Request(v["kind"], plan.clean_title(v.get("title")))
    if asks_for_options(text):
        return Request("options", "")
    return Request("question" if asks_a_question(text) else "change", "")


async def read_reply(harness: Harness, settings: Settings, text: str, found: list[Option]) -> Reply:
    """What Riggy's reply to some options says: a pick, or a wish for other options."""
    letters = [o.letter for o in found]
    if settings.harness != "fake" and text.strip():
        listed = "\n".join(f"{o.letter}: {o.title}. {o.description}" for o in found)
        v = await _ask(harness, settings, "pick", {"$options": listed, "$reply": text.strip()}, REPLY_SCHEMA)
        if v and v.get("decision") in ("pick", "more"):
            chosen = [str(x).upper() for x in v.get("picked") or []]
            return Reply(v["decision"] == "more", [x for x in dict.fromkeys(chosen) if x in letters])
    return plain_reply(text, letters)


async def _ask(harness: Harness, settings: Settings, name: str, values: dict[str, str], schema: dict) -> dict | None:
    """One cheap structured call with prompts/<name>.md; None if it didn't work out."""
    prompt = (settings.process_dir / "prompts" / f"{name}.md").read_text()
    for key, value in values.items():
        prompt = prompt.replace(key, value)
    cwd = settings.state_dir / "triage"
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        result = await harness.run(
            cwd=cwd,
            prompt=prompt,
            model=CALL_MODEL,
            max_turns=3,
            log_path=cwd / f"{name}.jsonl.gz",
            env={k: v for k, v in os.environ.items() if k not in DENY},
            schema=schema,
            label=name,
        )
    except Exception:
        log.exception("%s call failed", name)
        return None
    if result.outcome != "verdict" or not isinstance(result.verdict, dict):
        log.warning("%s call: %s %s", name, result.outcome, result.error)
        return None
    return result.verdict


def _short(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
