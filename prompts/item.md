Build one item of this step's checklist: **$title**

$do

- Files to start from: $files
- Its check, which golem runs after your step: `$check`. It exits 0 once the
  item works.

1. Read the files above and what they lean on; `AGENTS.md` says how the project
   is built, run and tested.
2. Build this item, and only this one: the items after it are other workers'
   (the whole list is below, so you can see where yours fits).
3. Run its check yourself until it passes, and keep the project's other tests
   passing (`bin/test`). Don't change the check's files ($checks): golem puts
   them back as they were before it checks.
4. If it changes what's on screen, look at it in the browser at phone size
   (skills/browser.md), and fix what looks wrong or clumsy.

The checklist (yours is $n of $of):

$list
$failed
Message: one sentence on what you built.
