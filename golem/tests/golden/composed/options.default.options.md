Make the options Riggy asked to see. Golem shows them to him on his phone,
lettered A, B, C… in the order you return them, and the next step builds the
one he picks from your files. Don't change the app itself in this step.

1. Work out what he wants to choose between, and how many options: as many as
   he asked for, otherwise three (at most six). Make them really different from
   each other and from what's there now, each one something you'd be happy to
   build. If the project has a design guide (`docs/design.md`), keep them within
   it unless he asked for something outside it. If there's been a round before (see "Done so far" and his feedback),
   this round answers what he said about that one.
2. Make each option concrete enough to judge from a picture. For anything he'd
   see (an art style, characters, a layout, colors, a logo), make it for real
   with the project's own stack and assets where you can, beside the app rather
   than in it: a preview page in the option's folder that draws a real scene or
   screen, say. Show the same scene in each option, so they compare side by
   side. Render it and take screenshots
   at phone size (skills/browser.md): one to three per option, PNG or JPEG,
   under 2 MB each. Look at them, and redo anything that doesn't show the idea
   well. For things he can't see (rules, wording, how something works),
   describe each option plainly; a picture only where it helps.
3. Put each option's files in its own folder: `docs/options/job-<n>/a/`, `b/`,
   and so on, where `<n>` is this job's number (`$GOLEM_JOB`); for a later
   round, `docs/options/job-<n>/round-2/a/`. Keep its screenshots there with
   whatever the next step should reuse: the sprites, styles or code you made,
   and a preview page if you built one. Commit them. Leave earlier rounds as
   they are.

Return `done` with `options`, in order: for each, a short `title`, a
`description` (one or two plain sentences: what it is and why he might pick it)
and `files` (paths from the repo root, the screenshots he should see first).
Message: one sentence introducing the choice ("Three chibi takes on the hero and
the first level.").
