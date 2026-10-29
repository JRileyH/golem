---
steps:
  - refresh
  - {agent: propose, turns: 80}
  - proposals
---
Golem had nothing else to do, so it asked you to look over one of Riggy's
projects and propose improvements: the engineering team bringing ideas to its
product manager. He gives each a thumbs up or down, often with why, and a thumbs
up becomes a change job. Nothing is committed, pushed or deployed here.

## propose

Propose up to three improvements to the project, through this run's lens.

1. Get to know it as it is now: `README.md`, `AGENTS.md`, `docs/` (the brief,
   specs, decisions, lessons), `git log`, the code, and the live site (`curl`,
   or the browser with skills/browser.md for how it looks and behaves).
2. Look hard through the lens. Find specific things in this project (a file, a
   screen, a flow, a measurement), not advice that would fit any project ("add
   more tests", "improve performance"). Try it or measure it where you can, and
   say what you saw.
3. Be novel. Propose nothing already on the list of proposals so far, or a
   variation of one, whether he accepted it, turned it down or hasn't decided.
   His reasons tell you what he values and what he doesn't want: use them to
   judge your ideas, not as a template. More of what he liked before isn't the
   goal; what nobody has thought of yet is.
4. Make each worth his time: something he'd plausibly say yes to, sized for one
   change job (an hour or two of work, not a rewrite). Fewer, better proposals
   beat filler. If the lens turns up nothing worth doing, propose nothing.
5. Change nothing: no edits, commits, pushes or deploys, and leave the checkout
   as you found it.

Return `done` with your proposals in `proposals`, each with:

- `title`: a few words, as he'll see it in a list.
- `what`: what to change, concretely enough that a worker could build it from
  this alone.
- `why`: what it gets him or the project, and what you saw that made you
  propose it.
- `size`: small, medium or large.

In `message`, say in a sentence what you looked at. Never return `blocked`:
nobody is waiting to answer.
