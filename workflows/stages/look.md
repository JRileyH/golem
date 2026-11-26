## does.answer

Answer Riggy's question.

1. Work out what he wants to know. Many questions need little or no looking: say
   what you know from the project's docs and what's happened lately, and stop.
2. Look only as much as the answer needs: the code, `README.md`, `AGENTS.md`,
   `docs/` (the brief, specs, decisions, lessons), `git log`, the live site
   (`curl`, or the browser with skills/browser.md when it's about how it looks or
   behaves), and `golem-deploy status` or `golem-deploy logs` for how it's
   running.
3. Change nothing: no edits, commits, pushes or deploys, and leave the checkout as
   you found it. If the honest answer is "that needs a change", say what you'd
   change; he can ask for it on the project's page.
4. Be quick. This is a question, not a job: a few minutes, not an hour.

Return `done` with the answer in `message`: plain text for his phone, no markdown
headings or tables, leading with the answer itself, as long as it needs and no
longer (usually a few sentences). Put a relevant URL in `link` if there is one: a
file on GitHub, or a page of the site. Use `blocked` only if you can't tell what
he's asking, with one short question back.

## does.propose

Propose the one improvement to the project most worth his time, through this
run's lens. Golem asks for one a turn (hourly, daily or weekly) so that he gets
a steady trickle to decide on, not a pile: of everything you find, pick the best.

1. Get to know it as it is now: `README.md`, `AGENTS.md`, `docs/` (the brief,
   specs, decisions, lessons), `git log`, the code, and the live site (`curl`,
   or the browser with skills/browser.md for how it looks and behaves).
2. Look hard through the lens. Find specific things in this project (a file, a
   screen, a flow, a measurement), not advice that would fit any project ("add
   more tests", "improve performance"). Try it or measure it where you can, and
   say what you saw.
3. Be novel. Propose nothing already on the list of proposals so far, or a
   variation of one, whether he accepted it, turned it down, dismissed it or
   hasn't decided. His reasons tell you what he values and what he doesn't
   want: use them to judge your ideas, not as a template. One he dismissed he
   cleared away without judging it: don't read it as a no. More of what he
   liked before isn't the goal; what nobody has thought of yet is.
4. Make it worth his time: something he'd plausibly say yes to, sized for one
   change job (an hour or two of work, not a rewrite). If the lens turns up
   nothing worth doing, propose nothing rather than filler.
5. Change nothing: no edits, commits, pushes or deploys, and leave the checkout
   as you found it.

Return `done` with your proposal as the one item in `proposals` (empty if
nothing is worth proposing). He decides on it from a card on his phone, in
seconds, so every field but the plan is a line he takes in at a glance: short,
specific to this project, plain text, and nothing said twice. The detail a
worker needs goes in `plan`, folded away until he opens it.

- `title`: the change in at most 8 words, starting with a verb ("Remember each
  recipe's units"), as he'll see it in a list.
- `gain`: what it gets him or the people who use it, in one sentence of at
  most 20 words. The outcome, not the mechanism ("Cooks stop re-picking cups
  on every visit").
- `changes`: one to four lines of at most 12 words each, on what will be
  different, as he'd notice it ("The unit choice sticks per recipe").
- `saw`: the evidence, in one sentence: what you found here that prompted it,
  with a file, a screen or a number ("The toggle resets on reload:
  src/Scaler.tsx keeps it in memory only").
- `tradeoff`: the main cost or risk of doing it, in a few words; empty if none
  is worth his attention.
- `size`: small, medium or large.
- `plan`: for the worker who builds it, not for him: the files, the approach
  and how to check it works, concretely enough to build it from this alone.

In `message`, say in a sentence what you looked at. Never return `blocked`:
nobody is waiting to answer.
