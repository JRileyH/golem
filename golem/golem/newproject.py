"""The New project page: Riggy names a project and says what it's for, and its first job starts.

He picks its name and address (filled in from the name until he changes it), what it's for,
which workflow for new projects it starts with (the fallback picked), the mode (when golem has
any: plan.MODES), and its tag; model and effort are under More. golem only writes its summary
and the first job's title (bot.new_project). A second form takes on a repo in the org that
golem didn't make (bot.adopt): it's checked over first, and if something's in the way, nothing
is made and the page says what.

What's filled in comes back with what's wrong with it, so nothing he wrote is lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from .store import EFFORTS, MODELS


@dataclass
class Choices:
    workflows: list[tuple[str, str, str]]  # (key, name, what it's for): for new projects
    fallback: str  # the workflow picked unless he picks another
    modes: list[str]  # golem's (plan.MODES): none to pick, no Mode field
    tags: list[str]
    org: str


@dataclass
class Filled:
    """A form as it was sent, and what's wrong with it, by field ("" for the form as a whole)."""

    values: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def get(self, name: str, default: str = "") -> str:
        return self.values.get(name, default)


def page(choices: Choices, new: Filled | None = None, adopting: Filled | None = None) -> str:
    new, adopting = new or Filled(), adopting or Filled()
    return new_form(choices, new) + adopt_form(choices, adopting) + SLUG_SCRIPT


def new_form(c: Choices, f: Filled) -> str:
    flows = "".join(
        f"<option value='{escape(k)}'{' selected' if f.get('workflow', c.fallback) == k else ''} title='{escape(about)}'>{escape(n)}</option>"
        for k, n, about in c.workflows
    )
    edited = " data-edited=1" if f.get("slug") else ""
    return (
        "<form method=post action=/new class='card new-form'>"
        + _error(f, "")
        + "<h2>A new project</h2>"
        + "<p class=sub>Name it and say what it's for; golem writes its summary and starts straight away.</p>"
        + _field("Name", f"<input id=new-name name=name required maxlength=60 autocomplete=off value='{escape(f.get('name'))}' placeholder='Tree Cards'>", f, "name")
        + _field("Address", f"<div class=slug-row><input id=new-slug name=slug maxlength=31 pattern='[a-z0-9][a-z0-9-]{{1,30}}' autocomplete=off"
                 f" value='{escape(f.get('slug'))}' placeholder='tree-cards'{edited}><span class=muted>its web address, and its repo in github.com/{escape(c.org)}</span></div>", f, "slug")  # fmt: skip
        + _field("What it's for", f"<textarea name=idea required rows=5 placeholder='A flashcard game for learning northeastern US trees from their leaves and bark…'>{escape(f.get('idea'))}</textarea>", f, "idea")
        + "<div class=new-row>"
        + _field("Workflow", f"<select name=workflow>{flows}</select>", f, "workflow")
        + _modes(c, f)
        + _field("Tag", "<select name=tag><option value=''>None</option>" + "".join(
            f"<option{' selected' if f.get('tag') == t else ''}>{escape(t)}</option>" for t in c.tags) + "</select>", f, "tag")  # fmt: skip
        + "</div>"
        + "<details class=new-more><summary>More</summary><div class=new-row>"
        + _field("Model", _pick("model", MODELS, f.get("model")), f, "model")
        + _field("Effort", _pick("effort", EFFORTS, f.get("effort")), f, "effort")
        + "</div></details>"
        + "<div class=row-form><button class=primary>Start it</button></div></form>"
    )


def adopt_form(c: Choices, f: Filled) -> str:
    opened = " open" if f.values or f.errors else ""
    return (
        f"<details class='card adopt'{opened}><summary>Adopt a repo golem didn't make</summary>"
        "<form method=post action=/new/adopt class=new-form>"
        + _error(f, "")
        + f"<p class=sub>A repo in github.com/{escape(c.org)}. golem checks it over first, takes it on if it can, and says what's in the way if it can't.</p>"
        + _field("Repo", f"<input name=repo required maxlength=31 autocomplete=off value='{escape(f.get('repo'))}' placeholder='mimic'>", f, "repo")
        + _field("What it is (optional)", f"<textarea name=words rows=2 placeholder='Cards for my table, by its README otherwise'>{escape(f.get('words'))}</textarea>", f, "words")
        + "<div class=new-row>"
        + _field("Name (optional)", f"<input name=name maxlength=60 value='{escape(f.get('name'))}' placeholder='From its README'>", f, "name")
        + _modes(c, f)
        + _field("Tag", "<select name=tag><option value=''>None</option>" + "".join(
            f"<option{' selected' if f.get('tag') == t else ''}>{escape(t)}</option>" for t in c.tags) + "</select>", f, "tag")  # fmt: skip
        + "</div><div class=row-form><button>Adopt it</button></div></form></details>"
    )


def _modes(c: Choices, f: Filled) -> str:
    """The Mode field: the usual way, or one of golem's modes; none at all while it has none."""
    if not c.modes:
        return ""
    current = f.get("mode")
    items = [("", "Usual"), *((m, m.capitalize()) for m in c.modes)]
    select = "<select name=mode>" + "".join(f"<option value='{escape(v)}'{' selected' if current == v else ''}>{escape(label)}</option>" for v, label in items) + "</select>"
    return _field("Mode", select, f, "mode")


def _pick(name: str, values: tuple[str, ...], current: str) -> str:
    return f"<select name={name}><option value=''>Each step's default</option>" + "".join(
        f"<option{' selected' if current == v else ''}>{escape(v)}</option>" for v in values
    ) + "</select>"


def _field(label: str, control: str, f: Filled, name: str) -> str:
    return f"<label class=field><span>{escape(label)}</span>{control}{_error(f, name)}</label>"


def _error(f: Filled, name: str) -> str:
    return f"<p class=error role=alert>{escape(f.errors[name])}</p>" if f.errors.get(name) else ""


# The address follows the name until it's changed by hand (intake.slugify's rule).
SLUG_SCRIPT = """<script>
(() => {
  const name = document.getElementById('new-name'), slug = document.getElementById('new-slug');
  if (!name || !slug) return;
  const slugify = (t) => { let s = t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 31).replace(/-+$/, ''); return s.length < 2 ? (s ? s + 'app' : '') : s; };
  slug.addEventListener('input', () => { slug.dataset.edited = slug.value ? '1' : ''; });
  name.addEventListener('input', () => { if (!slug.dataset.edited) slug.value = slugify(name.value); });
})();
</script>"""
