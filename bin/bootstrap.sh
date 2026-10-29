#!/usr/bin/env bash
# First-time setup for golem: `bin/bootstrap.sh server` on the box that serves it,
# `bin/bootstrap.sh local` on a laptop. Safe to re-run. Never prints secret values.
set -euo pipefail

mode=${1:-}
if [[ $mode != local && $mode != server ]]; then
  echo "usage: bin/bootstrap.sh local|server" >&2
  exit 2
fi
cd "$(dirname "$0")/.."

env_file=.env
if [[ ! -f $env_file ]]; then
  cp .env.example "$env_file"
  echo "created .env from .env.example"
fi
chmod 600 "$env_file"

# The value of KEY in .env (empty if missing).
get() {
  awk -v k="$1" 'index($0, k "=") == 1 { v = substr($0, length(k) + 2) } END { print v }' "$env_file"
}

# Set KEY=VALUE in .env, replacing any existing line.
put() {
  awk -v k="$1" -v v="$2" '
    index($0, k "=") == 1 { if (!done) print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }' "$env_file" > "$env_file.tmp"
  mv "$env_file.tmp" "$env_file"
  chmod 600 "$env_file"
}

# Remove KEY from .env, if it's there.
drop() {
  if [[ -n $(awk -v k="$1" 'index($0, k "=") == 1' "$env_file") ]]; then
    awk -v k="$1" 'index($0, k "=") != 1' "$env_file" > "$env_file.tmp"
    mv "$env_file.tmp" "$env_file"
    chmod 600 "$env_file"
    echo "removed $1"
  fi
}

# Set KEY only if it is missing or empty.
default() {
  if [[ -z $(get "$1") ]]; then
    put "$1" "$2"
    echo "set $1"
  fi
}

# Mode settings: always applied, so the file matches the box it's on.
mode_setting() {
  if [[ $(get "$1") != "$2" ]]; then
    put "$1" "$2"
    echo "set $1=$2"
  fi
}

if [[ -z $(get DEPLOYER_SECRET) ]]; then
  put DEPLOYER_SECRET "$(openssl rand -hex 32)"
  echo "generated DEPLOYER_SECRET"
fi
if [[ -z $(get DEPLOYER_ADMIN_SECRET) ]]; then
  put DEPLOYER_ADMIN_SECRET "$(openssl rand -hex 32)"
  echo "generated DEPLOYER_ADMIN_SECRET"
fi
default HARNESS claude
# Replaced by COMPOSE_FILE (local mode's compose.local.yml).
drop COMPOSE_PROFILES

case $mode in
  local)
    mode_setting BASE_DOMAIN localhost
    mode_setting COMPOSE_FILE compose.yml:compose.local.yml
    mode_setting DEPLOYER_GIT_SOURCE local
    mode_setting DEPLOYER_REQUIRE_CHECKS false
    # No GitHub locally; the org only names images (ghcr.io/<org>/<slug>).
    default GITHUB_ORG local
    # Not 80: local dev stacks often hold it.
    default EDGE_PORT 8800
    mkdir -p var/git
    ;;
  server)
    # Local mode's placeholders; a server needs real ones.
    [[ $(get BASE_DOMAIN) != localhost ]] || put BASE_DOMAIN ""
    [[ $(get GITHUB_ORG) != local ]] || put GITHUB_ORG ""
    drop COMPOSE_FILE
    mode_setting EDGE_PORT ""
    mode_setting DEPLOYER_GIT_SOURCE github
    mode_setting DEPLOYER_REQUIRE_CHECKS true
    mkdir -p var/git
    ;;
esac

missing=()
for key in CLAUDE_CODE_OAUTH_TOKEN GOLEM_WEB_PASSWORD; do
  [[ -n $(get "$key") ]] || missing+=("$key")
done
if [[ $mode == server ]]; then
  for key in BASE_DOMAIN GITHUB_ORG GOLEM_GITHUB_TOKEN DEPLOYER_GITHUB_TOKEN DEPLOYER_REGISTRY_USER DEPLOYER_REGISTRY_TOKEN; do
    [[ -n $(get "$key") ]] || missing+=("$key")
  done
fi
if (( ${#missing[@]} )); then
  echo "still to fill in by hand in .env: ${missing[*]}" >&2
fi

docker compose config --quiet
echo "ok: .env is complete enough for 'docker compose up -d --build'"
