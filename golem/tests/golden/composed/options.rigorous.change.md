Build what Riggy picked from the options the step before made. His pick and
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
   small change, write a short mini-spec in `docs/specs/` first.
3. Add or update tests for the paths you touch, and check the result in a real
   browser (skills/browser.md) at phone size, next to the option's screenshots.
4. Record the choice in `docs/decisions.md` (which option, and his words).
   Leave `docs/options/` as it is: it's the record of what he was shown.
