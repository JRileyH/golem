## makes.directions

Make a round of the project's design pass (skills/design.md): the doc, with
directions for Riggy to pick from, built on his words in `docs/brief.md` and
what the project is now. If there's been a round before (see "Done so far" and
his feedback), this round answers what he said about it. Don't change the app
itself in this step. Return what the skill says to return.

## makes.guide

Write the design guide, `docs/design.md`, and the real assets it lists
(skills/design.md): from the direction Riggy picked (under "Done so far", with
where its files are), his answers to the questions (his feedback), and his
words. If he didn't pick one (the pick was skipped or left to you), go with the
round's recommendation (the doc's decisions page) and say so in your message.
Add the line about it to `AGENTS.md`. Don't restyle the app yet.

## makes.restyle

Restyle the app from `docs/design.md` and its assets (skills/design.md): tokens
and fonts first, then every component, screen and state, the icons and
manifest. Check it in a real browser on a phone and wide, light and dark
(`golem-screenshot --dark`), beside the picked direction's pages in
`docs/design/rounds/`. Keep tests passing. If building it showed the guide was
wrong somewhere, fix the guide.
