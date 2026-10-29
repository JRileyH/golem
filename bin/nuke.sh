#!/usr/bin/env bash
# Start over with zero projects: `bin/nuke.sh`, in the golem checkout. Says what it
# will delete and asks first.
#
# Deletes every project the way !delete does (site, data, checkout, job history, run
# logs, conversation), every other site the deployer still has (hello too), and golem's
# lessons and proposals. Keeps the stack, golem's own conversation, and .env. GitHub
# repos stay: delete those on GitHub.
set -euo pipefail
cd "$(dirname "$0")/.."

nuke() {
  docker compose run --rm --no-deps golem python -m golem.nuke "$@"
}

status=0
nuke || status=$?
if (( status == 3 )); then
  exit 0  # nothing to delete
elif (( status != 0 )); then
  exit "$status"
fi

echo
answer=
read -r -p "Type nuke to delete all of that for good: " answer || true
if [[ $answer != nuke ]]; then
  echo "Nothing deleted."
  exit 1
fi

# No worker may be running while its project is deleted.
docker compose stop golem
trap 'docker compose start golem' EXIT
nuke --yes
