---
steps:
  - refresh
  - {agent: examine, turns: 60}
  - answer
---
A question about an existing project: Riggy wants to know something, not have
something built. Golem brings the checkout up to date, you look into it as much
as it needs, and golem sends him your answer. Nothing is committed, pushed or
deployed.

## examine

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
   change; he can ask for it in the conversation.
4. Be quick. This is a question, not a job: a few minutes, not an hour.

Return `done` with the answer in `message`: plain text for his phone, no markdown
headings or tables, leading with the answer itself, as long as it needs and no
longer (usually a few sentences). Put a relevant URL in `link` if there is one: a
file on GitHub, or a page of the site. Use `blocked` only if you can't tell what
he's asking, with one short question back.
