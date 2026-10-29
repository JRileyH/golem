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
# Where this checkout is, for upgrades from the web app (the deployer mounts it at this path).
mode_setting GOLEM_HOST_DIR "$(pwd)"
# Replaced by COMPOSE_FILE (compose.http.yml, below).
drop COMPOSE_PROFILES

case $mode in
  local)
    mode_setting BASE_DOMAIN localhost
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
    if [[ $(get BASE_DOMAIN) == localhost ]]; then
      put BASE_DOMAIN ""
      put EDGE_PORT ""
    fi
    [[ $(get GITHUB_ORG) != local ]] || put GITHUB_ORG ""
    mode_setting DEPLOYER_GIT_SOURCE github
    mode_setting DEPLOYER_REQUIRE_CHECKS true
    mkdir -p var/git
    ;;
esac

# The front door: golem's Traefik on 80 and 443 with its own certificates, or plain HTTP
# on EDGE_PORT behind something else (compose.http.yml).
edge=$(get EDGE_PORT)
if [[ -z $edge ]]; then
  drop COMPOSE_FILE
  echo "front door: golem takes ports 80 and 443 and gets its own certificates"
elif [[ $edge =~ ^([0-9]{1,3}(\.[0-9]{1,3}){3}:)?[0-9]{1,5}$ ]]; then
  mode_setting COMPOSE_FILE compose.yml:compose.http.yml
  if [[ $mode == local ]]; then
    echo "front door: http://golem.localhost:${edge##*:}"
  else
    echo "front door: plain HTTP on $edge, for your reverse proxy to forward to (docs/behind-a-proxy.md)"
  fi
else
  echo "EDGE_PORT must be a port (8800) or IPv4 address:port (127.0.0.1:8800), not $edge" >&2
  exit 1
fi

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
