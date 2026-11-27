"""Stage blocks: agent blocks that already know their job (Plan, Build, Review, Docs, Design,
Fix, Look, Options), set up with a few settings instead of a prompt.

The catalog, `workflows/stages/stages.yml`, says each stage's settings (the first option of each
is its default), whether it has a care setting (quick, usual, thorough), its usual turns and
turn, its contract or outputs, and what it leaves for later blocks to hold to. Its text is in
`workflows/stages/<stage>.md`, a `## <part>` section per part: `base`, `<setting>.<option>`,
`care.<level>`, and variants: a part used instead of another when About says what the step is
about calls for it (a Review's `plan`, of only planning work, and `fixes`, of the last review's
fixes, instead of its base; a Build's `pick`, of Riggy's pick, instead of `builds.auto`). A
stage whose settings say what it does (Plan, Build) has `base: after`, so its base says how
after. Whichever part says what its message should say, that comes last.

A stage block is an agent block with `stage`, its settings as keys of their own (`does:
reports`), `care`, `extra` (instructions of its own, added after golem's) and `about` (the
earlier block it's about, if not the one golem works out: about.py). `derive` writes the
outputs or contract its settings give it, so the engine walks it like any agent block.

golem's texts are the master prompt set: a mode's prompt set (modes.py) changes parts of them.
When a job starts, each of its stage blocks takes the parts it uses with it, in its mode's set
(workflows.parts_for), so changing a text never touches work under way; when its step runs,
`compose` puts them together with what the step is about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FOLDER = Path(__file__).resolve().parents[2] / "workflows" / "stages"  # in a checkout; golem says where otherwise (use)
CARE = ("quick", "usual", "thorough")
SECTION_RE = re.compile(r"^## +(\S+) *$", re.M)
EXTRA_HEAD = "Also, for this step:"
ABOUT_HEAD = "## What this step is about"
PARA_RE = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    options: tuple[tuple[str, str], ...]  # (value, what it means), the default first

    @property
    def default(self) -> str:
        return self.options[0][0]

    def allows(self, value: object) -> bool:
        return any(value == v for v, _ in self.options)


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    help: str
    settings: tuple[Setting, ...]
    care: bool
    optional: bool
    base_after: bool  # its setting's part says what, before its base says how (Build)
    variants: dict[str, tuple[str, str]]  # name: (the part it's used instead of, when)
    spec: dict = field(hash=False, compare=False)  # turns, turn, contract, outputs, produces as written

    def pick(self, name: str, node: dict, default: object = None) -> object:
        """A per-stage value (turns, turn, contract, outputs) for this block's settings: the one
        keyed by one of its `<setting>.<option>`s, else the stage's `default`, else the plain value."""
        value = self.spec.get(name, default)
        if not isinstance(value, dict):
            return value
        for s in self.settings:
            key = f"{s.key}.{node.get(s.key, s.default)}"
            if key in value:
                return value[key]
        return value.get("default", default)

    def produces(self, node: dict) -> list[str]:
        made = self.spec.get("produces") or {}
        return [p for s in self.settings for p in made.get(f"{s.key}.{node.get(s.key, s.default)}", [])]

    def parts(self, node: dict, variant: str = "") -> list[str]:
        """The names of the parts a block uses, in the order they go together."""
        chosen = [f"{s.key}.{node.get(s.key, s.default)}" for s in self.settings]
        found = [*chosen, "base"] if self.base_after else ["base", *chosen]
        if variant in self.variants and self.variants[variant][0] in found:
            found[found.index(self.variants[variant][0])] = variant
        if self.care:
            found.append(f"care.{node.get('care') or 'usual'}")
        return found

    def every_part(self, node: dict) -> set[str]:
        """The parts a block might use, whatever About finds: what a job takes with it."""
        return {*self.parts(node), *(p for v in self.variants for p in self.parts(node, v))}

    def summary(self, node: dict) -> str:
        """Its settings in a few words, for the graph: "reports · thorough"."""
        bits = [node.get(s.key, s.default) for s in self.settings]
        if self.care and (node.get("care") or "usual") != "usual":
            bits.append(node["care"])
        return " · ".join(str(b) for b in bits)


_LOADED: dict[tuple, tuple[dict[str, Stage], dict[str, dict[str, str]]]] = {}


_where = [FOLDER]


def use(folder: Path) -> None:
    """Where the catalog is: `<process dir>/workflows/stages` (workflows.Library says)."""
    _where[0] = folder


def load(folder: Path | None = None) -> tuple[dict[str, Stage], dict[str, dict[str, str]]]:
    """The stages and golem's text for each of their parts, read once per version of the files."""
    folder = folder or _where[0]
    stamp = (str(folder), *sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in folder.glob("*.*")))
    if stamp not in _LOADED:
        _LOADED.clear()
        _LOADED[stamp] = _load(folder)
    return _LOADED[stamp]


def _load(folder: Path) -> tuple[dict[str, Stage], dict[str, dict[str, str]]]:
    spec = yaml.safe_load((folder / "stages.yml").read_text())
    found, texts = {}, {}
    for key, s in spec.items():
        settings = tuple(
            Setting(name, str(v.get("label") or name), tuple((str(o), str(about)) for o, about in (v.get("options") or {}).items()))
            for name, v in (s.get("settings") or {}).items()
        )
        variants = {name: (str(v["instead"]), str(v.get("when", ""))) for name, v in (s.get("variants") or {}).items()}
        found[key] = Stage(key, s["label"], s.get("help", ""), settings, bool(s.get("care")), bool(s.get("optional")), s.get("base") == "after", variants, s)
        md = (folder / f"{key}.md").read_text() if (folder / f"{key}.md").exists() else ""
        pieces = SECTION_RE.split(md)
        texts[key] = {name: body.strip() for name, body in zip(pieces[1::2], pieces[2::2])}
        known = {"base", *variants, *(f"{st.key}.{o}" for st in settings for o, _ in st.options), *(f"care.{c}" for c in CARE)}
        if extra := set(texts[key]) - known:
            raise ValueError(f"stages/{key}.md: parts no setting uses: {', '.join(sorted(extra))}")
    return found, texts


def catalog() -> dict[str, Stage]:
    return load()[0]


def golems_text() -> dict[str, dict[str, str]]:
    return load()[1]


# --- blocks ------------------------------------------------------------------------------


def derive(node: dict) -> dict:
    """A stage block as the engine sees it: an agent block with the outputs or contract its
    settings give it, its stage's usual turns and turn where it doesn't say, and settings its
    stage doesn't have dropped. Changes `node` and returns it; other blocks are left alone."""
    stage = catalog().get(node.get("stage") or "")
    if node.get("type") != "agent" or stage is None:
        return node
    for s in stage.settings:
        if not s.allows(node.get(s.key)):
            node[s.key] = s.default
    if stage.care and node.get("care") not in CARE:
        node["care"] = "usual"
    elif not stage.care:
        node.pop("care", None)
    node.pop("instructions", None)  # golem's text, put together when it runs
    node.pop("contract", None)
    node.pop("outputs", None)
    if contract := stage.pick("contract", node):
        node["contract"] = contract
    if outputs := stage.pick("outputs", node):
        node["outputs"] = [dict(o) for o in outputs]
    if not node.get("turns"):
        node["turns"] = int(stage.pick("turns", node, 150))
    if "turn" not in node and (turn := stage.pick("turn", node)):
        node["turn"] = turn
    return node


def setting_keys() -> tuple[str, ...]:
    """Every stage's setting names: what a stage block keeps (workflows.KEEPS)."""
    return tuple(dict.fromkeys(s.key for stage in catalog().values() for s in stage.settings))


def compose(node: dict, parts: dict[str, str], about_text: str = "", variant: str = "") -> str:
    """What a stage block tells its agent: golem's parts for its settings, in order (what its
    message should say last), then its own extra instructions, then what the step is about."""
    stage = catalog().get(node.get("stage") or "")
    if stage is None:
        return node.get("instructions") or ""
    names = [stage.variants[n][0] if n in stage.variants and not (parts.get(n) or "").strip() else n for n in stage.parts(node, variant)]
    paras = [p.strip() for name in names for p in PARA_RE.split(parts.get(name, ""))]
    # What its message should say ends golem's text, whichever part said it.
    pieces = [p for p in paras if not p.startswith("Message:")] + [p for p in paras if p.startswith("Message:")]
    if extra := (node.get("extra") or "").strip():
        pieces.append(f"{EXTRA_HEAD}\n\n{extra}")
    if about_text.strip():
        pieces.append(f"{ABOUT_HEAD}\n\n{about_text.strip()}")
    return "\n\n".join(p.strip() for p in pieces if p and p.strip())
