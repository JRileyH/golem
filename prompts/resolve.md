Other work on this project shipped while this job was under way, or is shipping
with it, and it conflicts with this job's work. golem can't build or ship this job
until that work is merged into its branch:

$problem

1. Make each merge above, with the command given for it, then resolve every
   conflict.
2. Keep both changes' intent. What's on main is live and was reviewed; another
   job's branch was built and reviewed in that job. Never drop or undo either,
   and never take one side wholesale (`git checkout --ours/--theirs`, `-X ours`).
   Read the commits you're merging (`git log HEAD..origin/main`, or
   `git log HEAD..<the job's branch>`) to see what they were for, and make this
   job's work fit with them.
3. Run the tests and build the app the way CI does; fix what the merge broke.
4. Commit each merge.

Answer `blocked` only if two changes truly contradict each other (they want the
same thing to work two different ways), saying what each wants.

golem then checks the branch is up to date with main and carries on.
