"""Settings, the web app's top-level tab for golem itself: signing in, notifications, pausing
all work, how many workers run at once, the version, the tags, and golem's modes, with the one
requests run in by default. (Now and Projects are now.py's.)
"""

from __future__ import annotations

import json
from html import escape

from . import modes, pwa
from .pages import _hidden, action_form, select_form, signin_card
from .store import MAX_WORKERS, Tag
from .upgrade import SHOWN, Upgrades

TIGHT = 700 * 2**20  # less memory than this each and a worker's build and browser may not fit
# c1-c8, named for the mineral pigments they're the colors of: blue, orange, teal-green, yellow, pink, green, purple, red
COLORS = ("lapis", "rust", "verdigris", "ochre", "madder", "moss", "amethyst", "cinnabar")


# --- settings ---------------------------------------------------------------------------


def settings(signed_in: bool, paused: bool, found: dict[str, modes.Mode], default_mode: str, tags: list[Tag], counts: dict[str, int],
             upgrades: Upgrades | None = None, workers: int = 1, memory: int | None = None) -> str:  # fmt: skip
    if signed_in:
        account = (
            "<section class=card><h2>Signed in</h2><p class=sub>On this device. Sign out to stop it changing things.</p>"
            "<form method=post action=/logout class=row-form><input type=hidden name=back value=/settings>"
            "<button>Sign out</button></form></section>"
        )
        work = (
            "<section class=card><h2>All work</h2><p class=sub>"
            + ("Paused: nothing new starts until it's resumed." if paused else "Running: golem takes the next job when one finishes.")
            + "</p><div class=row-form>"
            + action_form("resume-all" if paused else "pause-all", "Resume all" if paused else "Pause all", back="/settings")
            + "</div></section>"
        )
        account += pwa.panel().replace("class=notify-panel", "class='card notify-panel'") + work + workers_card(workers, memory)
        account += version_card(upgrades) if upgrades else ""
    else:
        account = signin_card("/settings")
    more = (
        "<section class=card><ul class=links-list>"
        + "<li><a href=/insights>Insights</a><span class=sub>Where the time and Claude usage go, by kind of step, project and model.</span></li>"
        "<li><a href=/reports>Reports</a><span class=sub>What got in the way of golem's workers, about golem itself.</span></li>"
        + "<li><a href=/projects>Public projects page ↗</a><span class=sub>A card for each live project, for anyone.</span></li>"
        "</ul></section>"
    )
    return account + more + modes_card(found, default_mode, signed_in) + tags_card(tags, counts, signed_in)


def modes_card(found: dict[str, modes.Mode], default_mode: str, signed_in: bool) -> str:
    """golem's modes (modes.py), each what it's made of, and the one requests run in by default."""
    own = found.get(modes.DEFAULT)
    pick = ""
    if signed_in and own:
        others = tuple(k for k in found if k != modes.DEFAULT)
        pick = (
            "<div class=row-form><label>Requests run in</label>"
            + select_form("mode", default_mode if default_mode in others else "", others, f"{own.name} (golem's own)",
                          {k: found[k].name for k in others}, action="default-mode", back="/settings#modes")
            + "</div>"
        )
    items = "".join(_mode(m, m.key == (default_mode if default_mode in found else modes.DEFAULT)) for m in found.values())
    return (
        "<section class=card id=modes><h2>Modes</h2><p class=sub>How golem works: who does the agent steps, on which "
        "models, with which texts. A request runs in the mode it asks for, else its project's default, else golem's "
        "(below). Each mode is files in golem's repo (<code>modes/</code>), and a job keeps the mode it started in.</p>"
        f"{pick}<ul class=links-list>{items}</ul>"
        "<p class=hint><a href=/trials>Trials ›</a> run one request in two modes side by side, for you to pick one to ship.</p></section>"
    )


def _mode(m: modes.Mode, default: bool) -> str:
    steps = "".join(
        f"<li><b>{escape(k if k != '*' else 'every other step')}</b> {escape(c.model)}{escape('/' + c.effort) if c.effort else ''}</li>"
        for k, c in m.models.steps.items()
    )
    made = f"{'Claude Code' if m.harness == 'claude' else 'OpenCode'} · models: {m.models.name} · prompts: {m.prompts.name}"
    swaps = ", ".join(f"{a} → {b}" for a, b in m.workflows.items())
    return (
        f"<li><b>{escape(m.name)}</b>{' <span class=badge>default</span>' if default else ''}"
        f"<span class=sub>{escape(made)}{escape(' · workflows: ' + swaps) if swaps else ''}</span>"
        f"{f'<p class=hint>{escape(m.about)}</p>' if m.about else ''}"
        f"<details><summary>Models</summary><ul class=plain>{steps}</ul></details></li>"
    )


def workers_card(workers: int, memory: int | None) -> str:
    """How many steps run at once across projects, and what that leaves each of golem's memory."""
    options = "".join(f"<option{' selected' if n == workers else ''}>{n}</option>" for n in range(1, MAX_WORKERS + 1))
    pick = (
        f"<form method=post action=/act class=row-form>{_hidden(action='workers', back='/settings#workers')}"
        f"<label for=workers>At once</label><select id=workers name=value onchange='this.form.submit()'>{options}</select></form>"
    )
    if memory is None:
        room = "<p class=hint>golem has no memory cap here: its workers share all of this machine's memory.</p>"
    else:
        each = memory // workers
        room = (
            f"<p class=hint>golem's memory cap is {_size(memory)}, shared by golem and every worker"
            + (f": about {_size(each)} each at {workers}." if workers > 1 else ".")
            + "</p>"
        )
        if each < TIGHT:
            room += (
                "<p class=hint>That's tight for a build and a browser each: raise <code>GOLEM_MEM_LIMIT</code> in golem's "
                "<code>.env</code> (then <code>docker compose up -d</code>), or run fewer.</p>"
            )
    return (
        "<section class='card settings' id=workers><h2>Parallel workers</h2><p class=sub>How many agent steps run at once, "
        "across projects; each project's own steps still go one at a time. Each is a Claude Code process with its builds, "
        "dev servers and a browser, and more at once use your Claude plan faster.</p>" + pick + room + "</section>"
    )


def _size(n: int) -> str:
    return f"{n / 2**30:.1f} GB".replace(".0 GB", " GB") if n >= 2**30 else f"{n // 2**20} MB"


def version_card(upgrades: Upgrades) -> str:
    """golem's own version: what's running, what's new on GitHub, and upgrading to it."""
    back, v, state = "/settings", upgrades.version(), upgrades.state
    check = action_form("check-version", "Check now", back=back)
    auto = action_form("auto-upgrade", "Upgrade automatically: on" if upgrades.auto else "Upgrade automatically: off",
                       value="off" if upgrades.auto else "on", back=back)  # fmt: skip
    head = "<section class='card version'><h2>golem</h2>"
    if v is None:
        return head + f"<p class=sub>Not checked for a new version yet.</p><div class=row-form>{check}</div></section>"
    if not v.get("enabled"):
        return head + f"<p class=sub>{escape(upgrades.blocked(v))}</p></section>"
    on, behind = str(v.get("running") or v.get("checkout") or ""), v.get("behind", 0)
    running = on[:7]
    when = f" Checked {escape(str(v.get('checked', ''))[:16].replace('T', ' '))} UTC." if v.get("checked") else ""
    if behind:
        said = f"Running <code>{escape(running)}</code>; GitHub's main has {behind} new commit{'s' if behind != 1 else ''}.{when}"
    else:
        said = f"Running <code>{escape(running)}</code>, up to date.{when}"
    body = f"<p class=sub>{said}</p>"
    if behind:
        body += "<ul class=links-list>" + "".join(
            f"<li><code>{escape(c['sha'][:7])}</code>{escape(c['subject'])}</li>" for c in v.get("commits", [])[:SHOWN]
        ) + (f"<li class=sub>and {behind - SHOWN} more</li>" if behind > SHOWN else "") + "</ul>"
    if blocked := upgrades.blocked(v):
        body += f"<p class=hint>{escape(blocked)}</p>"
    last = v.get("upgrade") or {}
    if last.get("state") in ("failed", "rolled back") and last.get("from") == on:  # once golem's moved on (by hand), it's history
        body += f"<p class=hint>Last upgrade, to {escape(str(last.get('to', ''))[:7])}: {escape(last['state'])}. {escape(last.get('detail', ''))}</p>"
    if state == "pending":
        body += "<p class=hint>Upgrading once the running steps finish.</p>"
    elif state == "started":
        body += "<p class=hint>Upgrading: rebuilding and restarting, back in a few minutes.</p>"
    button = ""
    if behind and not blocked and not state:
        button = action_form("upgrade", "Upgrade now", back=back, confirm="Upgrade golem now? It restarts once nothing is running.")
    return head + body + f"<div class=row-form>{button}{check}{auto}</div></section>"


def tags_card(tags: list[Tag], counts: dict[str, int], signed_in: bool) -> str:
    """The sorts of project there are, each in its color on the projects page. One grid holds
    every tag's row, so a long name doesn't push its controls out of line with the others'."""
    back = "/settings#tags"
    rows = []
    for t in tags:
        n = counts.get(t.name, 0)
        row = f"<b><span class=swatch></span>{escape(t.name)}</b><span class=uses>{n} project{'' if n == 1 else 's'}</span>"
        if signed_in:
            colors = "".join(f"<option value={i}{' selected' if i == t.color else ''}>{c}</option>" for i, c in enumerate(COLORS, 1))
            row += (
                f"<form method=post action=/act>{_hidden(action='tag-color', name=t.name, back=back)}"
                f"<select name=value aria-label='Color of {escape(t.name)}' onchange='this.form.submit()'>{colors}</select></form>"
                f"<form method=post action=/act class=rename>{_hidden(action='tag-rename', name=t.name, back=back)}"
                f"<input name=value value='{escape(t.name)}' required maxlength=20 aria-label='New name for {escape(t.name)}'>"
                "<button>Rename</button></form>"
                f"<form method=post action=/act onsubmit='return confirm({escape(json.dumps(f'Delete the {t.name} tag?'))})'>"
                f"{_hidden(action='tag-delete', name=t.name, back=back)}<button class=danger>Delete</button></form>"
            )
        rows.append(f"<div class='tag c{t.color}'>{row}</div>")
    add = (
        f"<form method=post action=/act class=row-form>{_hidden(action='tag-add', back=back)}"
        "<input name=value placeholder='A new tag, like Dev Tool' required maxlength=20 aria-label='New tag'>"
        "<button>Add tag</button></form>"
        if signed_in
        else "<p class=hint>Sign in to change them.</p>"
    )
    return (
        "<section class='card settings' id=tags><h2>Tags</h2><p class=sub>The big-picture sorts of project, each in its "
        "color on the projects page. golem gives each new idea one of these, or none, and adds one only for a broad sort "
        "of project none of them covers. A topic or genre isn't a tag.</p>"
        + (f"<div class='tag-list{'' if signed_in else ' readonly'}'>{''.join(rows)}</div>" if rows else "<p class=empty>No tags.</p>")
        + add
        + "</section>"
    )
