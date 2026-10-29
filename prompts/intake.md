Riggy dictated an idea for a small web project into his phone. Turn it into a
brief and return it as the structured output. Don't use any other tools or ask
questions.

- name: the product name. **If he named it ("called Leaf Peeper", "name it
  Canopy"), use exactly that.** Otherwise make up a short, friendly one, 1 to 4
  words. Never use the first words of the dictation as a name.
- slug: the subdomain it will live at: lowercase letters, digits and hyphens,
  2 to 31 characters, usually the name ("leaf-peeper").
- summary: one to three sentences on what it is and who it's for, in plain words.
  Leave out instructions about how to build it (mode, model, effort).
  Fill obvious gaps sensibly; don't invent big features he didn't mention.
- tag: what sort of project it is, at the broadest level. Tags sort the public
  projects page into a handful of big groups, so there are very few of them.
  - Pick one of the tags below if it fits: `Game` for anything you play
    (a puzzle, a quiz game, an arcade game, a multiplayer game).
  - `none` if no tag below fits. This is the usual answer for anything that
    isn't a game, and the answer whenever you're unsure.
  - `new` only if it plainly belongs to a broad sort of project that none of
    the tags below covers and that many future projects would share, at the
    level of "Game": for example "Dev Tool" (for programmers) or "Service" (an
    API or bot other things use). Then put its name in new_tag: one or two
    words in Title Case, singular.
  - Never make a tag for a topic, genre, audience, style, platform or feature:
    not "Gardening", "Trees", "Pixel Art", "Puzzle", "Kids", "Mobile",
    "Multiplayer" or "Single Player". Those are what a project is about or
    how it works, not what sort of project it is.

  Tags there are now:

$tags

- new_tag: only when tag is `new`; otherwise leave it out.
- mode: `reckless`, `standard` or `rigorous` only if he asked for one (also
  "quick and dirty" means reckless, "be thorough" or "take your time" means
  rigorous). Otherwise `unspecified`.
- model: `fable`, `opus`, `sonnet` or `haiku` only if he named one ("use opus").
  Otherwise `unspecified`.
- effort: `low`, `medium`, `high`, `xhigh` or `max` only if he said one
  ("max effort", "extra high effort" means xhigh). Otherwise `unspecified`.

Dictation (speech-to-text, so expect typos and missing punctuation):

$dictation
