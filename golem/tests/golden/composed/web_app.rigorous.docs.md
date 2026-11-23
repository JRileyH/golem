1. `README.md` leads with the live URL and what the project does; `AGENTS.md`
   is accurate (stack, run, test, deploy, gotchas). Update them where the work
   this step is about (below) made them wrong, and `docs/design.md` if it
   changed the look (a new component, color or screen pattern later work should
   reuse).
2. Retro: add anything a future worker on this project should know to
   `docs/lessons.md`. Add lessons that apply to every project (a deploy gotcha,
   a CI trick) to the golem lessons file.

If a workflow or skill step was wrong or missing, write the edit you'd propose
to a new file in the golem proposals directory, for Riggy to apply by hand.

Message: one or two sentences on what Riggy can do on the site now. It's what
he reads when golem tells him it's done.

## What this step is about

The work since the job last shipped: `git log --oneline c98714408e76..HEAD` and `git diff c98714408e76..HEAD`.

What each step since said:
- buildout: (what buildout said)
- review: (what review said)
- second-pass: (what second-pass said)
