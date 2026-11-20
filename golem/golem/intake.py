"""Intake: a cheap triage call turns dictation into a brief (name, slug, summary, tag).

The tag is one of the few big-picture sorts of project there are ("Game"), or none. Only
rarely a new one: the prompt sets a high bar, and a new tag must look like a sort of
project (a word or two), or the project gets none.

A repo golem adopts (adopt.py) gets its brief the same way, from what Riggy said about it,
its README and its description; his words win. Its slug is the repo's name.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .harness import Harness
from .store import EFFORTS, MODELS, MODES
from .workspace import About

log = logging.getLogger(__name__)

# Same rules as the deployer's, so every slug golem picks deploys.
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,30}")
RESERVED = frozenset({"golem", "projects", "www", "status", "inbox", "traefik", "deployer", "api", "mail"})
TRIAGE_MODEL = "haiku"
TAG_RE = re.compile(r"[A-Z][A-Za-z]*( [A-Z][A-Za-z]*)?")  # a word or two in Title Case
TAG_CHARS = 20
README_CHARS = 6000  # of an adopted repo's README, for its brief


def schema(tags: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short product name, 1 to 4 words"},
            "slug": {"type": "string", "description": "Subdomain: lowercase letters, digits and hyphens, 2 to 31 chars"},
            "summary": {"type": "string", "description": "One to three sentences: what it is and who it's for"},
            "tag": {"enum": [*tags, "none", "new"]},
            "new_tag": {"type": "string", "description": "Only when tag is new: its name, one or two words"},
            "mode": {"enum": [*MODES, "unspecified"]},
            "model": {"enum": [*MODELS, "unspecified"]},
            "effort": {"enum": [*EFFORTS, "unspecified"]},
        },
        "required": ["name", "slug", "summary", "tag", "mode", "model", "effort"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class Brief:
    name: str
    slug: str
    summary: str
    tag: str = ""  # one of the tags, a new one (`new_tag`), or "" for none
    new_tag: bool = False
    # Only what the dictation asked for; None means use the default.
    mode: str | None = None
    model: str | None = None
    effort: str | None = None


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:31].rstrip("-")
    if len(slug) < 2:
        slug = f"{slug}app" if slug else "project"
    return slug


def valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.fullmatch(slug)) and not slug.endswith("-") and slug not in RESERVED


def fallback_brief(text: str) -> Brief:
    """No model: the first few words name the project. Used by the fake harness and if triage fails."""
    words = re.findall(r"[A-Za-z0-9']+", text)[:4] or ["project"]
    name = " ".join(words).title()
    return Brief(name, slugify(name), text.strip()[:500])


async def triage(
    harness: Harness, prompt_path: Path, text: str, cwd: Path, log_path: Path, env: dict, tags: Iterable[str] = ()
) -> Brief:
    tags = list(tags)
    prompt = _fill(prompt_path.read_text(), tags=_listed(tags), dictation=text.strip())
    v = await _ask(harness, prompt, schema(tags), cwd, log_path, env, "intake")
    if v is None:
        return fallback_brief(text)
    name = str(v.get("name") or "").strip()[:60] or fallback_brief(text).name
    slug = str(v.get("slug") or "").strip().lower()
    if not valid_slug(slug):
        slug = slugify(slug or name)
    return _brief(v, name, slug, text, tags)


def adopt_schema(tags: list[str]) -> dict:
    """An adopted repo's brief: the intake's, but its slug is the repo's name."""
    s = schema(tags)
    return {**s, "properties": {k: v for k, v in s["properties"].items() if k != "slug"}, "required": [r for r in s["required"] if r != "slug"]}


def adopt_fallback(slug: str, words: str, about: About) -> Brief:
    """No model: the README's heading names it; his words, the description or the README sum it up."""
    heading = next((line[2:].strip() for line in about.readme.splitlines() if line.startswith("# ")), "")
    paragraph = next((p.strip() for p in about.readme.split("\n\n") if p.strip() and not p.lstrip().startswith("#")), "")
    name = heading[:60] or slug.replace("-", " ").title()
    return Brief(name, slug, (words.strip() or about.description.strip() or paragraph or name)[:1000])


async def adopt_triage(
    harness: Harness, prompt_path: Path, slug: str, words: str, about: About, cwd: Path, log_path: Path, env: dict,
    tags: Iterable[str] = (),
) -> Brief:  # fmt: skip
    """The brief for a repo golem adopts: what Riggy said about it wins over its README and description."""
    tags = list(tags)
    prompt = _fill(
        prompt_path.read_text(), tags=_listed(tags), slug=slug, words=words.strip() or "(nothing)",
        description=about.description.strip() or "(none)", readme=about.readme[:README_CHARS].strip() or "(none)",
    )  # fmt: skip
    v = await _ask(harness, prompt, adopt_schema(tags), cwd, log_path, env, "adopt")
    fallback = adopt_fallback(slug, words, about)
    if v is None:
        return fallback
    return _brief(v, str(v.get("name") or "").strip()[:60] or fallback.name, slug, fallback.summary, tags)


async def _ask(harness: Harness, prompt: str, wanted: dict, cwd: Path, log_path: Path, env: dict, label: str) -> dict | None:
    """One cheap structured call; None if it didn't give an answer."""
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        result = await harness.run(
            cwd=cwd, prompt=prompt, model=TRIAGE_MODEL, max_turns=3, log_path=log_path, env=env, schema=wanted, label=label
        )
    except Exception:
        log.exception("%s triage failed", label)
        return None
    if result.outcome != "verdict" or not result.verdict:
        log.warning("%s triage %s: %s", label, result.outcome, result.error)
        return None
    return result.verdict


def _brief(v: dict, name: str, slug: str, summary: str, tags: list[str]) -> Brief:
    tag, new = _tag(v.get("tag"), v.get("new_tag"), tags)
    return Brief(
        name,
        slug,
        str(v.get("summary") or summary).strip()[:1000],
        tag=tag,
        new_tag=new,
        mode=_pick(v.get("mode"), MODES),
        model=_pick(v.get("model"), MODELS),
        effort=_pick(v.get("effort"), EFFORTS),
    )


def _fill(template: str, **values: str) -> str:
    """$name placeholders, in one pass: a README or a dictation that says `$tags` stays as it is."""
    return re.sub(r"\$(\w+)", lambda m: values.get(m[1], m[0]), template)


def _listed(tags: list[str]) -> str:
    return "\n".join(f"- {t}" for t in tags) or "(none yet)"


def _pick(value: object, allowed: tuple[str, ...]) -> str | None:
    return value if value in allowed else None


def _tag(picked: object, named: object, tags: list[str]) -> tuple[str, bool]:
    """(the tag, whether it's new). A new one that's an old one in other words is the old one."""
    if picked in tags:
        return picked, False
    if picked != "new":
        return "", False
    name = " ".join(str(named or "").split())
    name = " ".join(w[:1].upper() + w[1:] for w in name.split(" "))
    same = next((t for t in tags if _singular(t) == _singular(name)), None)
    if same:
        return same, False
    if len(name) > TAG_CHARS or not TAG_RE.fullmatch(name):
        return "", False
    return name, True


def _singular(tag: str) -> str:
    return tag.lower().removesuffix("s")


async def unique_slug(slug: str, taken: Callable[[str], Awaitable[bool]]) -> str:
    """`slug`, or `slug-2`, `slug-3`... until `await taken(candidate)` is false."""
    if valid_slug(slug) and not await taken(slug):
        return slug
    base = slug[:28].rstrip("-")
    if base in RESERVED or not valid_slug(base):
        base = slugify(f"{base}-app")[:28].rstrip("-")
    for n in range(2, 100):
        candidate = f"{base}-{n}"
        if not await taken(candidate):
            return candidate
    raise RuntimeError(f"no free slug near {slug}")
