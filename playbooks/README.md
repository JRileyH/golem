# Playbooks

The steps of a job, read by the scheduler and written into its database when the
job starts. Front matter lists the steps; each `## <name>` section is an agent
step's instructions. Every other step is golem's own code: `setup`, `push`,
`pr`, `checks`, `merge`, `push-main`, `deploy`, `checkin`, `choose`, `alert`,
`refresh`, `answer`. A playbook either ships something, starting with `setup` and
ending with `deploy, alert`, or only answers a question, starting with `refresh`
and ending with `answer` (the loader refuses anything else). The model and effort
are chosen in the web app: for a kind of step (all `docs` steps, say), a project,
or one step. Baked read-only into the golem image.

- `reckless.md`, `standard.md`, `rigorous.md`: new projects, by mode.
- `options.md`: changes he wants to choose first: an `options` step mocks up a
  few, golem's `choose` step shows them to him and waits, and the next step
  builds his pick (`golem/golem/options.py`).
- `feature.md`: changes Riggy asks for in a project's conversation (steps can be limited
  to some modes).
- `question.md`: questions about a project. Golem's `refresh` brings the
  checkout up to date (unless a job's work is under way in it), an `examine` step
  looks into it as much as it needs and changes nothing (golem puts the checkout
  back), and golem's `answer` sends him the answer. Its prompt is
  `prompts/question.md` rather than `step.md`.

`optional: true` lets Riggy skip a step from the status page. Use it for reviews,
docs, the check-in and the pick, never for the work itself; golem's push-to-deploy steps
can't be optional (the loader refuses it).

When CI fails or the deployer refuses a commit, golem inserts a `fix` agent
step (instructions in `prompts/fix.md`) and repeats the steps from the last push.
