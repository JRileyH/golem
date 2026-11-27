"""Modes: named ways for golem to work, each a set of its parts picked by name.

A mode (`modes/<key>.yml`) names a harness (who does the agent steps: Claude Code or OpenCode),
a model config (`modes/models/<key>.yml`: the model for each step, and the effort where the
harness has one), a prompt set (`modes/prompts/<key>/`: what it says differently from golem's own
texts) and any workflows it runs in place of golem's (`workflows/`). A job runs in one mode: the
one asked for, else its project's default, else golem's (Settings). When it starts it takes its
mode with it, resolved, in its own copy of its workflow (Scheduler.pin): the models, the prompt
set's texts in its stage blocks, the instructions in what every agent step is told. So changing a
mode never touches work under way, and a job's record says exactly what it ran: what a trial
compares.

The files have a fixed format, checked as they're read (a key golem doesn't know is refused), so
they map onto tables if modes move into the database:
- a mode: key, name, about, harness, models, prompts, workflows {workflow it replaces: workflow}
- a model config: key, name, about, harness, steps {"*" | <step name> | "stage:<stage>": {model, effort}}
- a prompt set: key, name, about, instructions, parts {(stage, part): text}

golem's own texts are the master prompt set (`golem`): prompts/ and workflows/stages/. A prompt set
holds only what it changes, so a new one starts as golem's and drifts from it a part at a time,
and gets what golem improves in the rest: a `## <part>` section in its `stages/<stage>.md`
replaces golem's part of that stage (workflows/stages/<stage>.md), and its `instructions.md` is
told to every agent step of a job, after its workflow's own text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import stages
from .store import EFFORTS, MODELS

FOLDER = Path(__file__).resolve().parents[2] / "modes"  # in a checkout; Settings.process_dir / "modes" in the image
HARNESSES = ("claude", "opencode")
DEFAULT = "claude-power"  # golem's default mode, until Riggy picks another (Settings)
MASTER = "golem"  # the prompt set that's golem's own texts: no folder, nothing changed
FREE = "free"  # an OpenCode model: whichever of OpenCode Zen's free models is offered when the job starts
MODE_KEYS = {"name", "about", "harness", "models", "prompts", "workflows"}
MODELS_KEYS = {"name", "about", "harness", "steps"}
PROMPTS_KEYS = {"name", "about"}
CHOICE_KEYS = {"model", "effort"}
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
VARIANT_RE = re.compile(r"^[a-z][a-z0-9-]{0,19}$")  # OpenCode's variants are its providers' words: high, max, minimal…


@dataclass(frozen=True)
class Choice:
    """The model a step runs, and its effort ("" for the harness's default, or where it has none)."""

    model: str
    effort: str = ""


@dataclass(frozen=True)
class Models:
    key: str
    name: str
    about: str
    harness: str
    steps: dict[str, Choice]  # by "*", a step's name, or "stage:<stage>"

    def choice(self, step: str, stage: str = "") -> tuple[Choice | None, str]:
        """For a step: its own entry, else its stage's, else every step's; and which it was."""
        for key, said in ((step, f"the {step} step"), (f"stage:{stage}" if stage else "", f"{stage} steps"), ("*", "every step")):
            if key and key in self.steps:
                return self.steps[key], said
        return None, ""


@dataclass(frozen=True)
class Prompts:
    key: str
    name: str
    about: str
    instructions: str = ""  # told to every agent step of a job
    parts: dict[str, dict[str, str]] = field(default_factory=dict)  # stage -> part -> its text, in place of golem's

    def stage_parts(self, stage: str, golems: dict[str, str]) -> dict[str, str]:
        """A stage's texts in this set: golem's, with this set's parts in their place."""
        return {**golems, **self.parts.get(stage, {})}


@dataclass(frozen=True)
class Mode:
    key: str
    name: str
    about: str
    harness: str
    models: Models
    prompts: Prompts
    workflows: dict[str, str] = field(default_factory=dict)  # a workflow it runs in place of golem's, by golem's key

    def record(self) -> dict:
        """What a job keeps of its mode when it starts: enough to run on and to say what it ran."""
        return {
            "key": self.key, "name": self.name, "harness": self.harness, "models": self.models.key,
            "steps": {k: [c.model, c.effort] for k, c in self.models.steps.items()}, "prompts": self.prompts.key,
            "workflows": dict(self.workflows),
        }  # fmt: skip


def chosen(asked: str = "", project_default: str = "", golems: str = "") -> Mode:
    """The mode a job runs in: the one asked for, else its project's default, else golem's
    (Riggy's setting), else DEFAULT; a key golem doesn't have (any more) is passed over."""
    found = load()
    for key in (asked, project_default, golems, DEFAULT):
        if key in found:
            return found[key]
    raise ValueError(f"golem has no mode {DEFAULT} (modes/)")


def kept(record: dict | None) -> Mode | None:
    """A job's mode, from what it kept when it started (Mode.record): its prompts went into its stage blocks then."""
    if not isinstance(record, dict) or not record.get("key"):
        return None
    steps = {k: Choice(str(v[0]), str(v[1] or "")) for k, v in (record.get("steps") or {}).items()}
    models = Models(str(record.get("models") or ""), "", "", str(record.get("harness") or "claude"), steps)
    prompts = Prompts(str(record.get("prompts") or MASTER), "", "")
    return Mode(record["key"], str(record.get("name") or record["key"]), "", models.harness, models, prompts, dict(record.get("workflows") or {}))


# --- reading the files -------------------------------------------------------------------------

_LOADED: dict[tuple, dict[str, Mode]] = {}
_where = [FOLDER]


def use(folder: Path) -> None:
    """Where the modes are: `<process dir>/modes` (workflows.Library says)."""
    _where[0] = folder


def load(folder: Path | None = None) -> dict[str, Mode]:
    """Every mode in the folder, by key, read once per version of the files."""
    folder = folder or _where[0]
    files = sorted(p for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []
    stamp = (str(folder), *((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files))
    if stamp not in _LOADED:
        _LOADED.clear()
        _LOADED[stamp] = _load(folder)
    return _LOADED[stamp]


def _load(folder: Path) -> dict[str, Mode]:
    models = {p.stem: _models(p) for p in sorted((folder / "models").glob("*.yml"))}
    prompts = {MASTER: Prompts(MASTER, "golem's own", "golem's texts as they are: prompts/ and workflows/stages/.")}
    prompts |= {p.name: _prompts(p) for p in sorted((folder / "prompts").glob("*")) if p.is_dir()}
    found = {}
    for path in sorted(folder.glob("*.yml")):
        spec = _spec(path, MODE_KEYS, {"name", "harness", "models"})
        if spec["harness"] not in HARNESSES:
            raise ValueError(f"{path.name}: harness is one of {', '.join(HARNESSES)}")
        config = models.get(spec["models"])
        if config is None:
            raise ValueError(f"{path.name}: there's no model config {spec['models']} (modes/models/)")
        if config.harness != spec["harness"]:
            raise ValueError(f"{path.name}: its model config is for {config.harness}, not {spec['harness']}")
        texts = prompts.get(spec.get("prompts") or MASTER)
        if texts is None:
            raise ValueError(f"{path.name}: there's no prompt set {spec['prompts']} (modes/prompts/)")
        workflows = spec.get("workflows") or {}
        if not isinstance(workflows, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in workflows.items()):
            raise ValueError(f"{path.name}: workflows is a map from golem's workflow to the one run in its place")
        found[path.stem] = Mode(path.stem, spec["name"], str(spec.get("about") or "").strip(), spec["harness"], config, texts, workflows)
    return found


def _spec(path: Path, allowed: set[str], required: set[str]) -> dict:
    if not KEY_RE.match(path.stem):
        raise ValueError(f"{path.name}: a key is lowercase letters, digits and dashes")
    spec = yaml.safe_load(path.read_text()) or {}
    if not isinstance(spec, dict):
        raise ValueError(f"{path.name}: not a map")  # noqa: TRY004 -- a file golem can't read, like the rest
    if unknown := set(spec) - allowed:
        raise ValueError(f"{path.name}: golem doesn't know {', '.join(sorted(unknown))}")
    if missing := required - set(spec):
        raise ValueError(f"{path.name}: it needs {', '.join(sorted(missing))}")
    return spec


def _models(path: Path) -> Models:
    spec = _spec(path, MODELS_KEYS, {"name", "harness", "steps"})
    harness = spec["harness"]
    if harness not in HARNESSES:
        raise ValueError(f"{path.name}: harness is one of {', '.join(HARNESSES)}")
    steps = {}
    for key, value in (spec["steps"] or {}).items():
        if not isinstance(value, dict) or set(value) - CHOICE_KEYS or "model" not in value:
            raise ValueError(f"{path.name}: {key} is {{model, effort}}")
        choice = Choice(str(value["model"]), str(value.get("effort") or ""))
        if problem := _wrong(harness, choice):
            raise ValueError(f"{path.name}: {key}: {problem}")
        steps[str(key)] = choice
    if "*" not in steps:
        raise ValueError(f"{path.name}: it needs a choice for every step (\"*\")")
    return Models(path.stem, spec["name"], str(spec.get("about") or "").strip(), harness, steps)


def _wrong(harness: str, choice: Choice) -> str:
    """What's wrong with a model and effort for a harness, if anything."""
    if harness == "claude":
        if choice.model not in MODELS:
            return f"Claude Code's models are {', '.join(MODELS)}"
        if choice.effort and choice.effort not in EFFORTS:
            return f"its efforts are {', '.join(EFFORTS)}"
    elif harness == "opencode":
        if choice.model != FREE and "/" not in choice.model:
            return f"OpenCode's models are provider/model, or {FREE}"
        if choice.effort and not VARIANT_RE.match(choice.effort):
            return "an effort is a word (its provider's variant: high, max…)"
    return ""


def _prompts(folder: Path) -> Prompts:
    if not KEY_RE.match(folder.name) or folder.name == MASTER:
        raise ValueError(f"modes/prompts/{folder.name}: a key is lowercase letters, digits and dashes, and not {MASTER}")
    about = folder / "prompts.yml"
    spec = _spec(about, PROMPTS_KEYS, {"name"}) if about.exists() else {"name": folder.name}
    known = stages.golems_text()
    parts: dict[str, dict[str, str]] = {}
    for path in sorted((folder / "stages").glob("*.md")):
        stage = path.stem
        if stage not in known:
            raise ValueError(f"modes/prompts/{folder.name}/stages/{path.name}: there's no stage {stage}")
        text = path.read_text()
        names, bodies = stages.SECTION_RE.findall(text), stages.SECTION_RE.split(text)[2::2]
        if unknown := set(names) - set(known[stage]):
            raise ValueError(f"modes/prompts/{folder.name}/stages/{path.name}: {stage} has no part {', '.join(sorted(unknown))}")
        parts[stage] = {n: b.strip() for n, b in zip(names, bodies, strict=True) if b.strip()}
    instructions = folder / "instructions.md"
    return Prompts(folder.name, spec["name"], str(spec.get("about") or "").strip(),
                   instructions.read_text().strip() if instructions.exists() else "", parts)  # fmt: skip


# --- what Riggy set before modes, as a mode's files ---------------------------------------------


def export(store) -> str:
    """The model defaults and stage texts Riggy set in Settings before modes, written as a model
    config and a prompt set's stage files, to save in modes/ (and name a mode for). "" if he set
    nothing golem's own mode doesn't already say."""
    own = load()[DEFAULT].models.steps
    steps = {k: Choice(m, e) for k, (m, e) in store.defaults().items() if m or e}
    changed = {k: c for k, c in steps.items() if (k not in own or own[k] != Choice(c.model or own[k].model, c.effort))}
    out = []
    if changed:
        merged = {**{k: [c.model, c.effort] for k, c in own.items()}, **{k: [c.model or own.get(k, own["*"]).model, c.effort] for k, c in changed.items()}}
        lines = "\n".join(f'  "{k}": {{model: {m}{", effort: " + e if e else ""}}}' for k, (m, e) in merged.items())
        out.append(f"# modes/models/riggy.yml\nname: Riggy's\nabout: The model defaults Riggy set in Settings before modes.\nharness: claude\nsteps:\n{lines}\n")
    edited: dict[str, list[str]] = {}
    for (stage, part), row in sorted(store.stage_texts().items()):
        if row["edited"]:
            edited.setdefault(stage, []).append(f"## {part}\n\n{row['text'].strip()}\n")
    for stage, parts in edited.items():
        out.append(f"# modes/prompts/riggy/stages/{stage}.md\n" + "\n".join(parts))
    return "\n".join(out)


if __name__ == "__main__":  # python -m golem.modes export <golem.db>: what Riggy set before modes
    import sys

    from .store import Store

    if len(sys.argv) != 3 or sys.argv[1] != "export":
        sys.exit("usage: python -m golem.modes export <golem.db>")
    print(export(Store.read_only(sys.argv[2])) or "Nothing set before modes that golem's own mode doesn't already say.")
