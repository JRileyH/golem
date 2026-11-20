Riggy asked golem to adopt `$slug`, a repo golem didn't make, and look after it
from now on like a project golem built. Write its brief from what he said about
it, its README and its GitHub description, and return it as the structured
output. Don't use any other tools or ask questions.

**What he said wins.** Where his words say what it is, who it's for or what to
call it, use them, even over the README. Fill in the rest from the README and
the description.

- name: the product name. What he called it, else what the README calls it (its
  first heading, usually), else a short, friendly one. 1 to 4 words.
- summary: one to three sentences on what it is and who it's for, in plain words.
  Leave out how it's built, run or deployed, and instructions about how to work
  on it (mode, model, effort).
- tag: what sort of project it is, at the broadest level. Tags sort the public
  projects page into a handful of big groups, so there are very few of them.
  - Pick one of the tags below if it fits: `Game` for anything you play.
  - `none` if no tag below fits. This is the usual answer for anything that
    isn't a game, and the answer whenever you're unsure.
  - `new` only if it plainly belongs to a broad sort of project that none of
    the tags below covers and that many future projects would share, at the
    level of "Game": for example "Dev Tool" (for programmers) or "Service" (an
    API or bot other things use). Then put its name in new_tag: one or two
    words in Title Case, singular.
  - Never make a tag for a topic, genre, audience, style, platform or feature.

  Tags there are now:

$tags

- new_tag: only when tag is `new`; otherwise leave it out.
- mode: `reckless`, `standard` or `rigorous` only if he asked for one (also
  "quick and dirty" means reckless, "be thorough" or "take your time" means
  rigorous). Otherwise `unspecified`.
- model: `fable`, `opus`, `sonnet` or `haiku` only if he named one. Otherwise
  `unspecified`.
- effort: `low`, `medium`, `high`, `xhigh` or `max` only if he said one ("extra
  high effort" means xhigh). Otherwise `unspecified`.

What he said after `!adopt $slug` (maybe dictated, so expect typos):

$words

The repo's description on GitHub:

$description

Its README (the start of it):

$readme
