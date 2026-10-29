"""Cards for the projects page at projects.<base domain>: what each live project is, in a
line and a sentence, with a screenshot of it. Its tag is the project's (intake.py), not the card's.

A cheap model call reads the project's README and brief and writes the words; a headless
browser takes the screenshot. A card is made when a project first goes live and again
at the end of every job, so it follows the project as it grows. Neither is allowed to
hold up or fail a job: without them the page shows the project's summary and a
placeholder.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from .config import Settings
from .harness import Harness
from .store import Card, Project, Store

log = logging.getLogger(__name__)

CARD_MODEL = "haiku"
KINDS = ("game", "app", "tool", "site")
ACTIONS = {"game": "Play", "app": "Open", "tool": "Use", "site": "Visit"}  # the card's button
SCHEMA = {
    "type": "object",
    "properties": {
        "tagline": {"type": "string", "description": "What it is in a few catchy words, at most 60 characters"},
        "description": {"type": "string", "description": "One or two sentences for a visitor, at most 200 characters"},
        "kind": {"enum": list(KINDS)},
    },
    "required": ["tagline", "description", "kind"],
    "additionalProperties": False,
}
DOCS = ("README.md", "docs/brief.md")  # read from the project's checkout, this much of each
DOCS_CHARS = 4000
SHOT_SECONDS = 90
# Kept from the worker's environment: the model call needs Claude's token, nothing else of golem's.
DENY = frozenset({"DEPLOYER_ADMIN_SECRET", "GOLEM_WEB_PASSWORD", "DEPLOYER_SECRET", "GH_TOKEN"})

Shooter = Callable[[str, Path], Awaitable[bool]]


def fallback(project: Project) -> Card:
    """The card before there's a better one: the project's own summary."""
    return Card(project.slug, "", project.summary, "app")


class Cards:
    def __init__(self, settings: Settings, store: Store, harness: Harness, shoot: Shooter | None = None):
        self.settings = settings
        self.store = store
        self.harness = harness
        self.shoot = shoot or screenshot

    @property
    def dir(self) -> Path:
        return self.settings.state_dir / "cards"

    def image_path(self, card: Card) -> Path | None:
        return self.dir / card.image if card.image else None

    async def refresh(self, project: Project, checkout: Path) -> Card:
        """Write the project's card again and take a new screenshot; keep what can't be redone."""
        old = self.store.card(project.slug)
        card = await self.describe(project, checkout) or old or fallback(project)
        image = f"{project.slug}.jpg"
        if await self.shoot(self.settings.project_url(project.slug), self.dir / image):
            card.image = image
        elif old:
            card.image = old.image
        return self.store.put_card(card)

    async def describe(self, project: Project, checkout: Path) -> Card | None:
        requests = [j.request for j in reversed(self.store.jobs(limit=200)) if j.slug == project.slug][-5:]
        docs = []
        for name in DOCS:
            try:
                docs.append(f"## {name}\n\n{(checkout / name).read_text(errors='replace')[:DOCS_CHARS]}")
            except OSError:
                continue
        template = (self.settings.process_dir / "prompts" / "card.md").read_text()
        prompt = (
            template.replace("$name", project.name)
            .replace("$summary", project.summary)
            .replace("$requests", "\n\n".join(f"> {r.strip()}" for r in requests) or "(nothing recorded)")
            .replace("$docs", "\n\n".join(docs) or "(none)")
        )
        work = self.dir / "work"
        work.mkdir(parents=True, exist_ok=True)
        try:
            result = await self.harness.run(
                cwd=work,
                prompt=prompt,
                model=CARD_MODEL,
                max_turns=3,
                log_path=work / "last.jsonl.gz",
                env={k: v for k, v in os.environ.items() if k not in DENY},
                schema=SCHEMA,
                label="card",
            )
        except Exception:
            log.exception("card for %s failed", project.slug)
            return None
        v = result.verdict if result.outcome == "verdict" else None
        if not v:
            log.warning("card for %s: %s %s", project.slug, result.outcome, result.error)
            return None
        kind = v.get("kind") if v.get("kind") in KINDS else "app"
        return Card(
            project.slug,
            str(v.get("tagline") or "").strip()[:80],
            str(v.get("description") or project.summary).strip()[:300],
            kind,
        )


async def screenshot(url: str, path: Path) -> bool:
    """The top of the live site at desktop size, as a JPEG (golem/tools/golem-card-shot.mjs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}")
    try:
        proc = await asyncio.create_subprocess_exec(
            "golem-card-shot", url, str(tmp), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except OSError as e:
        log.warning("no screenshot of %s: %s", url, e)
        return False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), SHOT_SECONDS)
    except TimeoutError:
        proc.kill()
        log.warning("screenshot of %s timed out", url)
        return False
    if proc.returncode != 0 or not tmp.exists():
        log.warning("no screenshot of %s: %s", url, out.decode(errors="replace").strip()[-300:])
        return False
    tmp.replace(path)
    return True
