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

from .cards import ACTIONS
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
<meta name=theme-color content="#f6f4ef" media="(prefers-color-scheme: light)">
<meta name=theme-color content="#121212" media="(prefers-color-scheme: dark)">
<title>Projects · {escape(org)}</title><style>{STYLE}</style>
<header class=hero><p class=eyebrow>{escape(org)}</p><h1>Projects</h1>
<p class=lede>Games, apps and experiments, built and shipped by AI agents. Pick one and try it.</p>{filters}</header>
<main class=grid>{cards}</main>
<footer>{len(entries)} live · made with golem</footer>
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


STYLE = """
:root { color-scheme: light dark; --bg:#f6f4ef; --card:#fffdf9; --ink:#17150f; --soft:#5d5a52; --muted:#8a867c; --line:rgb(23 21 15 / .09);
  --cta:#17150f; --cta-ink:#fffdf9; --shadow:0 1px 2px rgb(23 21 15 / .06), 0 8px 24px rgb(23 21 15 / .06);
  --c1:#2a78d6; --c2:#eb6834; --c3:#1baf7a; --c4:#eda100; --c5:#e87ba4; --c6:#008300; --c7:#6250d6; --c8:#e34948; }
@media (prefers-color-scheme: dark) { :root { --bg:#121212; --card:#1c1c1b; --ink:#f1efe9; --soft:#c3c0b6; --muted:#8f8b81;
  --line:rgb(255 255 255 / .08); --cta:#f1efe9; --cta-ink:#121212; --shadow:0 1px 2px rgb(0 0 0 / .4), 0 10px 30px rgb(0 0 0 / .3);
  --c1:#3987e5; --c2:#d95926; --c3:#199e70; --c4:#c98500; --c5:#d55181; --c6:#008300; --c7:#9085e9; --c8:#e66767; } }
.c1 { --pc:var(--c1); } .c2 { --pc:var(--c2); } .c3 { --pc:var(--c3); } .c4 { --pc:var(--c4); }
.c5 { --pc:var(--c5); } .c6 { --pc:var(--c6); } .c7 { --pc:var(--c7); } .c8 { --pc:var(--c8); }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-font-smoothing:antialiased; }
a { color:inherit; text-decoration:none; }
.hero { max-width:72rem; margin:0 auto; padding:clamp(2.5rem, 8vw, 5rem) max(1.25rem, env(safe-area-inset-left)) 1.5rem; }
.eyebrow { margin:0 0 .4rem; font-size:.8rem; font-weight:600; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); }
h1 { margin:0; font-size:clamp(2.4rem, 7vw, 3.6rem); line-height:1.05; letter-spacing:-.03em; font-weight:750; }
.lede { margin:.75rem 0 0; max-width:34rem; font-size:1.1rem; color:var(--soft); }
.filters { display:flex; gap:.4rem; flex-wrap:wrap; margin-top:1.5rem; }
.filters button { font:inherit; font-size:.9rem; padding:.4rem .95rem; border-radius:999px; border:1px solid var(--line);
  background:var(--card); color:var(--soft); cursor:pointer; }
.filters button.on { background:var(--ink); color:var(--bg); border-color:var(--ink); }
.grid { max-width:72rem; margin:0 auto; padding:.5rem max(1.25rem, env(safe-area-inset-left)) 3rem; display:grid; gap:1.5rem;
  grid-template-columns:repeat(auto-fill, minmax(min(100%, 20rem), 1fr)); }
.card { display:flex; flex-direction:column; background:var(--card); border-radius:22px; overflow:hidden; box-shadow:var(--shadow);
  border:1px solid var(--line); transition:transform .25s ease, box-shadow .25s ease; }
.card:hover { transform:translateY(-3px); box-shadow:0 2px 4px rgb(0 0 0 / .06), 0 18px 40px rgb(0 0 0 / .12); }
.card[hidden] { display:none; }
.shot { display:block; position:relative; aspect-ratio:16 / 10; overflow:hidden; background:color-mix(in srgb, var(--pc) 18%, var(--card));
  border-bottom:1px solid var(--line); }
.shot img { width:100%; height:100%; object-fit:cover; object-position:top; display:block; transition:transform .5s ease; }
.card:hover .shot img { transform:scale(1.03); }
.placeholder { position:absolute; inset:0; display:grid; place-items:center;
  background:radial-gradient(120% 90% at 20% 10%, color-mix(in srgb, var(--pc) 55%, white) 0%, var(--pc) 55%, color-mix(in srgb, var(--pc) 70%, black) 100%); }
.placeholder span { font-size:4.5rem; font-weight:800; color:rgb(255 255 255 / .92); letter-spacing:-.04em; text-shadow:0 4px 18px rgb(0 0 0 / .18); }
.body { display:flex; flex-direction:column; flex:1; padding:1.1rem 1.25rem 1.25rem; }
.tag { align-self:flex-start; margin:0 0 .7rem; font-size:.75rem; font-weight:600; padding:.2rem .6rem; border-radius:999px;
  background:color-mix(in srgb, var(--pc) 16%, transparent); color:var(--ink); }
.tag::before { content:""; display:inline-block; width:.45rem; height:.45rem; border-radius:50%; background:var(--pc);
  margin-right:.35rem; vertical-align:.05rem; }
h2 { margin:0; font-size:1.35rem; line-height:1.2; letter-spacing:-.015em; font-weight:700; }
.tagline { margin:.2rem 0 0; color:var(--soft); font-weight:500; }
.desc { margin:.6rem 0 1.1rem; color:var(--soft); font-size:.95rem; }
.foot { margin-top:auto; display:flex; align-items:center; justify-content:space-between; gap:.75rem; }
.cta { display:inline-flex; align-items:center; gap:.45rem; padding:.65rem 1.25rem; border-radius:999px; background:var(--cta); color:var(--cta-ink);
  font-weight:650; font-size:.95rem; transition:gap .2s ease, transform .2s ease; }
.cta:hover { gap:.7rem; } .cta:active { transform:scale(.97); }
.cta:focus-visible, h2 a:focus-visible, .filters button:focus-visible { outline:3px solid var(--pc, var(--c1)); outline-offset:3px; }
.when { font-size:.8rem; color:var(--muted); }
.empty { color:var(--muted); grid-column:1 / -1; text-align:center; padding:3rem 0; }
footer { text-align:center; color:var(--muted); font-size:.8rem; padding:0 1rem 2.5rem; }
@media (prefers-reduced-motion: reduce) { .card, .shot img, .cta { transition:none; } .card:hover { transform:none; } }
"""

SCRIPT = """
document.querySelectorAll('.filters button').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.filters button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.card').forEach(c => { c.hidden = !!b.dataset.tag && c.dataset.tag !== b.dataset.tag; });
}));
"""
