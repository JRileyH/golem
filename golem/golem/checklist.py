"""Checklist mode (plan.MODES): a Build block plans its work as items, and smaller runs build them.

In a job in checklist mode, each Build stage block runs as a planner, on its usual model: it
splits what the step is about into items small enough for one short run, each with a check
golem can run (a command that exits 0 once the item works and not before), writes and commits
the checks, and returns the items. Golem then puts in, after it, a step that builds each item
(on the `item` default: haiku at medium effort until Riggy sets another) and a step that runs
its check. An item whose check fails is built again with what failed: once more on the item
model, then once on the Build block's own. After that golem leaves it failed and carries on,
and tells the next agent step which items didn't pass. Before each check golem puts the
planner's check files back as it wrote them, so an item can't pass by changing its test.

Each item and check step carries its item (store.Step.item): which plan it's from, its place
in it, what to build and how it's checked, which try it is and the model it runs on. The plan's
check files are kept with the job's logs, under `checklist-<the planner's step id>/`.

Pure but for reading and writing those files.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .harness import VERDICT_SCHEMA
from .store import Step

MODE = "checklist"
ABOUT = "a Build plans its work as items with checks, and cheaper runs build them one at a time"
ITEM_TURNS = 80
MAX_ITEMS = 16
TRIES = 3  # builds of one item before golem gives up on it: all but the last on the item model
DEFAULT = ("haiku", "medium")  # the item model until Riggy sets one (plan.SUGGESTED)
MAX_CHECK_BYTES = 256_000  # a check file bigger than this isn't kept (nor put back)
TEXT_CHARS = 4000  # of an item's instructions

SCHEMA = {
    **VERDICT_SCHEMA,
    "properties": {
        **VERDICT_SCHEMA["properties"],
        "items": {
            "type": "array",
            "maxItems": MAX_ITEMS,
            "description": "The work, in the order to build it",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The item in a few words"},
                    "do": {"type": "string", "description": "What to build, concretely enough to start on straight away"},
                    "files": {"type": "array", "items": {"type": "string"}, "description": "The files to touch or read first"},
                    "check": {"type": "string", "description": "A command, run in the checkout, that exits 0 once the item works and not before"},
                },
                "required": ["title", "do", "files", "check"],
                "additionalProperties": False,
            },
        },
        "checks": {"type": "array", "items": {"type": "string"}, "description": "The test files you wrote for the checks, as paths in the repo"},
    },
}


def twin_slug(slug: str) -> str:
    """Where a project built both ways has its checklist twin."""
    return f"{slug}-list"


def planning(job_mode: str, block: dict, step: Step) -> bool:
    """Whether a step plans a checklist: a Build stage block's, in a job in checklist mode."""
    return job_mode == MODE and block.get("stage") == "build" and not step.item


def collect(v: dict) -> tuple[list[dict], list[str], str]:
    """The items and check files a planner returned, tidied; or what's wrong with them."""
    items = []
    for raw in v.get("items") or []:
        if not isinstance(raw, dict):
            continue
        title, do, check = (" ".join(str(raw.get(k) or "").split()) for k in ("title", "do", "check"))
        if not (title and do and check):
            continue
        files = [str(f).strip() for f in raw.get("files") or [] if str(f).strip()][:20]
        items.append({"title": title[:120], "do": str(raw.get("do")).strip()[:TEXT_CHARS], "files": files, "check": check[:500]})
    if not items:
        return [], [], "The plan has no items with a title, what to do and a check: return them in `items`."
    checks = [str(c).strip() for c in v.get("checks") or [] if str(c).strip()][:40]
    return items[:MAX_ITEMS], checks, ""


def item_steps(plan_step: Step, items: list[dict], checks: list[str], model: tuple[str, str], up: tuple[str, str]) -> list[Step]:
    """A build step and a check step for each item, in order, to put in after the plan: building
    on `model` (the item default), and on `up` (the Build block's) for the last try."""
    out = []
    for n, raw in enumerate(items, start=1):
        item = {**raw, "plan": plan_step.id, "n": n, "of": len(items), "checks": checks, "try": 1, "up": list(up)}
        out += build_and_check(item, model)
    return out


def build_and_check(item: dict, model: tuple[str, str]) -> list[Step]:
    """One try at an item: its build, on `model`, then its check."""
    item = {**item, "model": list(model)}
    build = Step(0, 0, 0, "agent", "item", ITEM_TURNS, model=model[0], effort=model[1], turn="build", item=item)
    return [build, Step(0, 0, 0, "check", "check", turn="build", item=item)]


def again(item: dict, problem: str) -> list[Step] | None:
    """The next try at an item whose check failed, with what failed; None once it's had its tries."""
    tries = int(item.get("try") or 1)
    if tries >= TRIES:
        return None
    nxt = {**item, "try": tries + 1, "problem": problem}
    model = tuple(item["up"]) if tries + 1 == TRIES else tuple(item.get("model") or DEFAULT)
    return build_and_check(nxt, model)  # type: ignore[arg-type]


def name(item: dict) -> str:
    """An item as golem says it: "3/8 Quiz screen"."""
    return f"{item.get('n')}/{item.get('of')} {item.get('title')}"


def listed(items: list[dict], here: int) -> str:
    """The plan's items, one line each, the one at `here` marked: where an item fits."""
    return "\n".join(f"{'→' if n == here else ' '} {n}. {i.get('title')}" for n, i in enumerate(items, start=1))


def gave_up(item: dict, problem: str) -> str:
    """What the next agent step is told about an item golem gave up on."""
    return f"- {name(item)} (its check: `{item.get('check')}`) still failed after {item.get('try')} tries. The last check said:\n\n{problem}"


# --- the plan's check files ------------------------------------------------------------------


def folder(logs: Path, plan_id: int) -> Path:
    return logs / f"checklist-{plan_id}"


def keep(where: Path, checkout: Path, items: list[dict], checks: list[str]) -> list[str]:
    """The plan written down with the job's logs, and its check files copied there as they are
    now; the paths it kept (those in the checkout, not too big)."""
    root, kept = checkout.resolve(), []
    for name_ in checks:
        path = (root / name_).resolve()
        if not path.is_relative_to(root) or not path.is_file() or ".git" in path.relative_to(root).parts:
            continue
        if path.stat().st_size > MAX_CHECK_BYTES:
            continue
        rel = path.relative_to(root).as_posix()
        dest = where / "files" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        kept.append(rel)
    where.mkdir(parents=True, exist_ok=True)
    (where / "plan.json").write_text(json.dumps({"items": items, "checks": kept}, indent=2))
    return kept


def items_of(where: Path) -> list[dict]:
    try:
        return json.loads((where / "plan.json").read_text())["items"]
    except (OSError, ValueError, KeyError):
        return []


def restore(where: Path, checkout: Path) -> list[str]:
    """The plan's check files put back where an item changed or removed them; the paths it put back."""
    files, root, put = where / "files", checkout.resolve(), []
    if not files.is_dir():
        return put
    for kept in sorted(p for p in files.rglob("*") if p.is_file()):
        rel = kept.relative_to(files).as_posix()
        path = (root / rel).resolve()
        if not path.is_relative_to(root):
            continue
        if not path.is_file() or path.read_bytes() != kept.read_bytes():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(kept, path)
            put.append(rel)
    return put
