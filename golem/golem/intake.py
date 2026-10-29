"""Intake: a cheap triage call turns dictation into a brief (name, slug, summary, tag).

The tag is one of the few big-picture sorts of project there are ("Game"), or none. Only
rarely a new one: the prompt sets a high bar, and a new tag must look like a sort of
project (a word or two), or the project gets none.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .harness import Harness
from .store import EFFORTS, MODELS, MODES

log = logging.getLogger(__name__)

# Same rules as the deployer's, so every slug golem picks deploys.
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,30}")
RESERVED = frozenset({"golem", "projects", "www", "status", "inbox", "traefik", "deployer", "api", "mail"})
TRIAGE_MODEL = "haiku"
TAG_RE = re.compile(r"[A-Z][A-Za-z]*( [A-Z][A-Za-z]*)?")  # a word or two in Title Case
TAG_CHARS = 20


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
    listed = "\n".join(f"- {t}" for t in tags) or "(none yet)"
    prompt = prompt_path.read_text().replace("$tags", listed).replace("$dictation", text.strip())
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        result = await harness.run(
            cwd=cwd,
            prompt=prompt,
            model=TRIAGE_MODEL,
            max_turns=3,
            log_path=log_path,
            env=env,
            schema=schema(tags),
            label="intake",
        )
    except Exception:
        log.exception("triage failed")
        return fallback_brief(text)
    if result.outcome != "verdict" or not result.verdict:
        log.warning("triage %s: %s", result.outcome, result.error)
        return fallback_brief(text)
    v = result.verdict
    name = str(v.get("name") or "").strip()[:60] or fallback_brief(text).name
    slug = str(v.get("slug") or "").strip().lower()
    if not valid_slug(slug):
        slug = slugify(slug or name)
    tag, new = _tag(v.get("tag"), v.get("new_tag"), tags)
    return Brief(
        name,
        slug,
        str(v.get("summary") or text).strip()[:1000],
        tag=tag,
        new_tag=new,
        mode=_pick(v.get("mode"), MODES),
        model=_pick(v.get("model"), MODELS),
        effort=_pick(v.get("effort"), EFFORTS),
    )


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
