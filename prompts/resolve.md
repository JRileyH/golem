Other work on this project shipped while this job was under way, and it conflicts with
this job's work. golem can't build or ship this job until main is merged into its
branch:

$problem

1. `git fetch origin && git merge origin/main`, then resolve every conflict.
2. Keep both changes' intent. What's on main is live and was reviewed: never drop it
   or undo it, and never take one side wholesale (`git checkout --ours/--theirs`,
   `-X ours`). Read the commits on main (`git log HEAD..origin/main`) to see what
   they were for, and make this job's work fit with them.
3. Run the tests and build the app the way CI does; fix what the merge broke.
4. Commit the merge.

Answer `blocked` only if the two changes truly contradict each other (they want
the same thing to work two different ways), saying what each wants.

golem then checks the branch is up to date with main and carries on.
