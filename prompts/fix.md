Something failed after the last step, and golem can't go on until it's fixed:

$problem

1. Find the cause. Reproduce it locally if you can: run the tests, or build the
   app the way the Dockerfile and the CI workflow do.
2. Fix the cause and commit. Don't skip, delete or weaken tests or the workflow
   to get a green build.
3. If the deployer rejected `deploy/compose.yml`, its rules are in
   skills/deploy.md. If the site didn't answer after deploying, the logs above
   (or `golem-deploy logs`) say why.

golem then pushes your fix and repeats the steps that failed.
