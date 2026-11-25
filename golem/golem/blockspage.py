"""Settings → Blocks: golem's stage blocks (stages.py) and the text of each of their parts, to
change or put back to golem's."""

from __future__ import annotations

from html import escape

from . import stages


def blocks_page(blocks) -> str:
    """Settings → Blocks: golem's stage blocks, each with what it's for and its parts."""
    rows = []
    for stage in stages.catalog().values():
        parts = blocks.parts(stage.key)
        marks = []
        if n := sum(p.edited for p in parts):
            marks.append(f"<span class=badge>{n} changed</span>")
        if any(p.upstream for p in parts):
            marks.append("<span class='badge waiting'>golem has newer text</span>")
        if any(p.tampered for p in parts):
            marks.append("<span class='badge trouble'>changed outside golem</span>")
        settings = "; ".join(f"{escape(s.label.lower())}: {escape(', '.join(v for v, _ in s.options))}" for s in stage.settings)
        rows.append(
            f"<li><a href='/settings/blocks/{stage.key}'><b>{escape(stage.label)}</b></a>{''.join(marks)}"
            f"<p>{escape(stage.help)}</p><p class=muted>{settings or 'no settings'}{' · care' if stage.care else ''}</p></li>"
        )
    return (
        "<p class=lead>Golem's stage blocks: the agent steps of its workflows (workflows/ in golem's repo), each set up there "
        "with a few settings. Golem puts a step's text together from the parts below, with what the step is about. Change a part "
        "here and every block of that stage uses it, from the next job; Reset puts golem's back.</p>"
        f"<section class=card><ul class=workflows>{''.join(rows)}</ul></section>"
    )


def stage_page(blocks, key: str) -> str:
    """One stage's parts, each to change or put back to golem's."""
    stage = stages.catalog()[key]
    what = {"base": "Every block of the stage", **{v: f"{when}, instead of {instead}" for v, (instead, when) in stage.variants.items()}}
    for s in stage.settings:
        for value, means in s.options:
            what[f"{s.key}.{value}"] = f"{s.label}: {means}"
    for level in stages.CARE:
        what[f"care.{level}"] = f"Care: {level}"
    cards = []
    for p in blocks.parts(key):
        marks = (" <span class=badge>changed</span>" if p.edited else "") + (
            " <span class='badge waiting'>golem has newer text</span>" if p.upstream else "") + (
            " <span class='badge trouble'>changed outside golem: golem's is used</span>" if p.tampered else "")  # fmt: skip
        reset = (f"<form method=post action=/act class=inline>{_fields(action='block-reset', stage=key, part=p.name, back=f'/settings/blocks/{key}#{p.name}')}"
                 "<button class=quiet>Use golem's</button></form>") if p.edited or p.tampered else ""  # fmt: skip
        newer = f"<details><summary>Golem's text</summary><pre class=told>{escape(p.golems)}</pre></details>" if p.edited and p.golems else ""
        cards.append(
            f"<section class='card block-part' id='{escape(p.name)}'><h3><code>{escape(p.name)}</code> <span class=muted>{escape(what.get(p.name, ''))}</span>{marks}</h3>"
            f"<form method=post action=/act class=stack>{_fields(action='block-text', stage=key, part=p.name, back=f'/settings/blocks/{key}#{p.name}')}"
            f"<textarea name=value rows={min(24, max(3, p.text.count(chr(10)) + 2))} placeholder='Nothing: golem adds nothing for this. Write something to add it.'>{escape(p.text)}</textarea>"
            f"<div class=row-form><button>Save</button>{reset}</div></form>{newer}</section>"
        )
    return (
        f"<p class=lead>{escape(stage.help)}</p>"
        "<p class=hint>$problem fills in with what failed before the step. Golem adds what the step is about after these, "
        "and a block's own extra instructions before that.</p>"
        + "".join(cards)
    )


def _fields(**values: str) -> str:
    return "".join(f"<input type=hidden name={k} value='{escape(v)}'>" for k, v in values.items())
