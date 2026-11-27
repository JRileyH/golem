# Modes

A mode is how golem works on a job, by name. It is a set of golem's parts, each picked
by name:
- **a harness:** who does the agent steps
- **a model config:** which model, and what effort, each step runs
- **a prompt set:** what golem's texts say differently
- **workflow swaps:** any workflows it runs in place of golem's

A request runs in the mode it picks, else its project's default, else golem's (Settings →
Modes; `claude-power` until changed). When a job starts it keeps its mode, resolved, in
its own copy of its workflow. Changing a mode never touches work under way, and a job's
record says exactly what it ran. `golem/golem/modes.py` reads these files.

The files have a fixed format, and golem refuses a key it doesn't know. That keeps them
easy to turn into database tables later.

## A mode: `<key>.yml`

```yaml
name: OpenCode free          # shown in the web app
about: What it's for.        # optional
harness: opencode            # claude (Claude Code) or opencode (OpenCode)
models: opencode-free        # a model config: models/<key>.yml, for the same harness
prompts: golem               # a prompt set: prompts/<key>/; golem (golem's own texts) if left out
workflows: {change: lean}    # optional: a workflow (workflows/<key>.yml) run in place of golem's, same start type
```

## A model config: `models/<key>.yml`

```yaml
name: Claude power
about: Optional.
harness: claude
steps:                        # "*" is required: every step not named
  "*": {model: opus, effort: high}
  docs: {model: sonnet, effort: medium}   # a step, by its name (spec, mvp, review, docs, fix…)
  stage:build: {model: opus}              # a stage block's step, by its stage, after its own name
```

A step's model comes from the first of these that it finds:
1. the step's own pick (its sheet in the web app, before it starts)
2. its workflow block's own setting
3. its entry here by name
4. its stage's entry
5. `"*"`

What a harness can run:
- **Claude Code:** `fable`, `opus`, `sonnet` or `haiku`. Effort is `low`, `medium`, `high`, `xhigh` or `max`; leave it out for `GOLEM_EFFORT`.
- **OpenCode:** a `provider/model` (as `opencode models` lists them), or `free`, which is whichever of OpenCode Zen's free models is on offer when the job's first step runs, kept for the whole job. Effort is the provider's "variant" word (`high`, `max`, `minimal`…), and a model without variants ignores it.

## A prompt set: `prompts/<key>/`

golem's own texts are the master set, `golem`: `prompts/` and `workflows/stages/`. A
prompt set holds only what it changes. A new set starts out the same as golem's, then
differs one part at a time, and still gets golem's improvements in every part it left alone.

```
prompts/<key>/
  prompts.yml          # optional: name, about
  instructions.md      # optional: told to every agent step of the job, after its workflow's own text
  stages/<stage>.md    # `## <part>` sections replacing golem's parts of workflows/stages/<stage>.md
```

A part is one of the stage's own sections: `base`, `<setting>.<option>`, `care.<level>`, or
a variant. To make a "rigorous" set, for example, write the review stage's `care.thorough` and
`care.usual` parts in `stages/review.md`.

## Bringing over what was set before modes

Settings used to hold each kind of step's model and effort, and Settings → Blocks held
changed stage texts. To print those as files to save here, run
`python -m golem.modes export <golem.db>` on the server, in the golem container:
`docker compose run --rm --no-deps -T golem python -m golem.modes export /var/lib/golem/golem.db`.
