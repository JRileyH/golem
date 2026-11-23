Build what Riggy picked from the options an earlier step made. His pick and
where its files are is under "Done so far"; what he said about it is under
"Riggy's feedback".

1. Start from the picked option's files in `docs/options/`: they're what he
   chose, so match them, and reuse the sprites, styles and code there rather
   than starting again. Make any changes he asked for ("B, but bigger eyes"), or
   combine options if that's what he picked. If he didn't pick one (the pick
   was skipped or left to you), build the strongest and say which in your
   message.
2. Carry it through the whole app: every screen, asset and state the choice
   touches, not just what the screenshots showed. For anything bigger than a
   small change, write a short mini-spec in `docs/specs/` first. Check the
   result next to the option's screenshots.
3. Record the choice in `docs/decisions.md` (which option, and his words).
   Leave `docs/options/` as it is: it's the record of what he was shown.

Add or update tests for the paths you touch, and keep all the project's tests
passing: `bin/test` runs them (if the project has none yet, add one as
skills/new-repo.md says). Check the result in a real browser as you go
(skills/browser.md), at phone and desktop sizes, and look at the screenshots:
fix what looks wrong or feels clumsy.

Keep tests light: the image build is the only required check.

## What this step is about

What Riggy picked: (what choose said) Build that, from its files.
