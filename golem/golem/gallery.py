"""The projects page at projects.<base domain>: a card for each live project, for anyone.

Each card has a screenshot, the project's tag (in the tag's color, the same on every card),
name, tagline and description, and a button to it that says what you'll do there: Play a
game, Open an app. The words and screenshot come from cards.py; until a project has a card,
its summary stands in. With more than one sort of project live, buttons at the top show
one tag's, or the untagged ("Other").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from . import brand
from .cards import ACTIONS
from .pages import FONT_PRELOADS, TOKENS
from .store import Card, Project, Tag


@dataclass
class Entry:
    project: Project
    card: Card
    url: str
    image: str | None  # the screenshot's URL
    live_at: str  # when it last went live
    color: int  # 1-8, as on the status page
    tag: Tag | None = None


def page(entries: list[Entry], now: datetime, org: str, tags: list[Tag] = ()) -> str:
    """`tags`: all of them, in the order their filters go."""
    shown = [t.name for t in tags if any(e.tag and e.tag.name == t.name for e in entries)]
    groups = [(t, t) for t in shown] + ([("Other", "-")] if shown and any(not e.tag for e in entries) else [])
    filters = ""
    if len(groups) > 1:
        filters = "<nav class=filters aria-label='Show'><button class=on data-tag=''>All</button>" + "".join(
            f"<button data-tag='{escape(key)}'>{escape(label)}</button>" for label, key in groups
        ) + "</nav>"
    cards = "".join(card(e, now) for e in entries) or "<p class=empty>Nothing's live yet. Check back soon.</p>"
    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name=description content="Games, apps and experiments, built and shipped by AI agents.">
<meta name=theme-color content="#dde0df" media="(prefers-color-scheme: light)">
<meta name=theme-color content="#14181a" media="(prefers-color-scheme: dark)">
{FONT_PRELOADS}<title>Projects · {escape(org)}</title><style>{STYLE}</style><link rel=icon href=/favicon.svg type="image/svg+xml">
<header class=hero><p class=eyebrow>{escape(org)}</p><h1>Projects</h1>
<p class=lede>Games, apps and experiments, built and shipped by AI agents. Pick one and try it.</p>{filters}</header>
<main class=grid>{cards}</main>
<footer>{len(entries)} live · made with {brand.mark(small=True)} Golem</footer>
<script>{SCRIPT}</script></html>"""


def card(e: Entry, now: datetime) -> str:
    p, c = e.project, e.card
    action = ACTIONS.get(c.kind, "Open")
    tag = f"<p class='tag c{e.tag.color}'>{escape(e.tag.name)}</p>" if e.tag else ""
    shot = (
        f"<img src='{escape(e.image)}' alt='' loading=lazy decoding=async>"
        if e.image
        else f"<div class=placeholder><span>{escape(p.name[:1].upper())}</span></div>"
    )
    when = ""
    if e.live_at:
        days = (now - datetime.fromisoformat(e.live_at)).days
        when = "Updated today" if days < 1 else f"Updated {_days(days)}"
    tagline = f"<p class=tagline>{escape(c.tagline)}</p>" if c.tagline else ""
    return f"""<article class='card c{e.color}' data-tag='{escape(e.tag.name if e.tag else "-")}'>
<a class=shot href='{escape(e.url)}' tabindex=-1 aria-hidden=true>{shot}</a>
<div class=body>{tag}
<h2><a href='{escape(e.url)}'>{escape(p.name)}</a></h2>{tagline}
<p class=desc>{escape(c.description)}</p>
<div class=foot><a class=cta href='{escape(e.url)}'>{action} <span aria-hidden=true>→</span></a><span class=when>{when}</span></div></div>
</article>"""


def _days(days: int) -> str:
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days}d ago"
    if days < 60:
        return f"{days // 7}w ago"
    return f"{days // 30}mo ago"


STYLE = TOKENS + """
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 var(--body); -webkit-font-smoothing:antialiased; }
body::before { content:""; display:block; height:6px; background:var(--ember); }
a { color:inherit; text-decoration:none; }
.hero { max-width:72rem; margin:0 auto; padding:clamp(2.5rem, 8vw, 5rem) max(1.25rem, env(safe-area-inset-left)) 1.5rem; }
.eyebrow { display:flex; align-items:center; gap:.5rem; margin:0 0 .5rem; font-size:.8rem; font-weight:700; letter-spacing:.14em;
  text-transform:uppercase; color:var(--accent); }
.eyebrow::before { content:""; flex:none; width:.75rem; height:.5rem; background:var(--accent); }
h1 { margin:0; font:700 clamp(2.6rem, 8vw, 4rem)/1.02 var(--display); letter-spacing:-.01em; }
.lede { margin:.75rem 0 0; max-width:34rem; font-size:1.1rem; color:var(--muted); }
.filters { display:flex; gap:.4rem; flex-wrap:wrap; margin-top:1.5rem; }
.filters button { font:inherit; font-size:.9rem; font-weight:600; padding:.4rem .95rem .5rem; border:0; border-radius:var(--radius);
  background:var(--stone); color:var(--ink); cursor:pointer; box-shadow:inset 0 -3px 0 var(--lip); }
.filters button.on { background:var(--ink); color:var(--card); }
.grid { max-width:72rem; margin:0 auto; padding:.5rem max(1.25rem, env(safe-area-inset-left)) 3rem; display:grid; gap:1.5rem;
  grid-template-columns:repeat(auto-fill, minmax(min(100%, 20rem), 1fr)); }
.card { display:flex; flex-direction:column; background:var(--card); border-radius:var(--radius); overflow:hidden; box-shadow:0 3px 0 var(--edge);
  transition:transform .2s ease, box-shadow .2s ease; }
.card:hover { transform:translateY(-3px); box-shadow:0 6px 0 var(--edge); }
.card[hidden] { display:none; }
.shot { display:block; position:relative; aspect-ratio:16 / 10; overflow:hidden; background:color-mix(in srgb, var(--pc) 18%, var(--card));
  border-bottom:4px solid var(--pc); }
.shot img { width:100%; height:100%; object-fit:cover; object-position:top; display:block; transition:transform .5s ease; }
.card:hover .shot img { transform:scale(1.03); }
.placeholder { position:absolute; inset:0; display:grid; place-items:center;
  background:linear-gradient(135deg, color-mix(in srgb, var(--pc) 72%, white), var(--pc) 50%, color-mix(in srgb, var(--pc) 70%, black)); }
.placeholder span { font:700 5rem/1 var(--display); color:rgb(255 255 255 / .92); }
.body { display:flex; flex-direction:column; flex:1; padding:1.1rem 1.25rem 1.25rem; }
.tag { align-self:flex-start; display:flex; align-items:center; gap:.45rem; margin:0 0 .6rem; font-size:.75rem; font-weight:700; letter-spacing:.08em;
  text-transform:uppercase; color:var(--muted); }
.tag::before { content:""; flex:none; width:.8rem; height:.5rem; border-radius:1px; background:var(--pc); }
h2 { margin:0; font:700 1.45rem/1.2 var(--display); }
.tagline { margin:.2rem 0 0; font-weight:500; }
.desc { margin:.6rem 0 1.1rem; color:var(--muted); font-size:.95rem; }
.foot { margin-top:auto; display:flex; align-items:center; justify-content:space-between; gap:.75rem; }
.cta { display:inline-flex; align-items:center; gap:.45rem; padding:.6rem 1.15rem .7rem; border-radius:var(--radius); background:var(--accent) var(--rust);
  color:var(--accent-ink); font-weight:600; font-size:.95rem; box-shadow:inset 0 -3px 0 var(--lip-strong); transition:gap .2s ease; }
.cta:hover { gap:.7rem; } .cta:active { padding-top:.7rem; padding-bottom:.6rem; box-shadow:inset 0 -1px 0 var(--lip-strong); }
.cta:focus-visible, h2 a:focus-visible, .filters button:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
.when { font-size:.8rem; color:var(--muted); }
.empty { color:var(--muted); grid-column:1 / -1; text-align:center; padding:3rem 0; }
footer { display:flex; justify-content:center; align-items:center; gap:.35rem; color:var(--muted); font-size:.8rem; padding:0 1rem 2.5rem; }
footer .mark { width:1.05rem; height:1.05rem; }
@media (prefers-reduced-motion: reduce) { .card, .shot img, .cta { transition:none; } .card:hover { transform:none; } }
"""

SCRIPT = """
document.querySelectorAll('.filters button').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.filters button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.card').forEach(c => { c.hidden = !!b.dataset.tag && c.dataset.tag !== b.dataset.tag; });
}));
"""
