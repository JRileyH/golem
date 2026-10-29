set dotenv-load

default:
    @just --list

# Run both test suites
test:
    cd deployer && uv run pytest -q
    cd golem && uv run pytest -q

# First-time setup: fills in .env for this box (local or server)
bootstrap mode:
    bin/bootstrap.sh {{mode}}

# Prove Claude Code runs headless in the golem image on the subscription token
claude-check:
    docker compose run --rm --no-deps golem claude -p "Reply with exactly: golem ok" --output-format json --max-turns 1 \
        | jq '{result, is_error, model: (.modelUsage | keys), cost_usd: .total_cost_usd}'

# Ask the deployer to deploy a commit (the secret is expanded inside the golem container)
deploy slug sha:
    docker compose exec -T golem sh -c 'curl -sS -X POST http://deployer:8000/deploy \
        -H "X-Deployer-Secret: $DEPLOYER_SECRET" -H "Content-Type: application/json" \
        -d "{\"slug\": \"{{slug}}\", \"sha\": \"{{sha}}\"}"' | jq .

# Deployer status for a project
status slug:
    docker compose exec -T golem sh -c 'curl -sS "http://deployer:8000/status?slug={{slug}}" \
        -H "X-Deployer-Secret: $DEPLOYER_SECRET"' | jq .

# Stop a project (keeps its volumes)
stop slug:
    docker compose exec -T golem sh -c 'curl -sS -X POST http://deployer:8000/stop \
        -H "X-Deployer-Secret: $DEPLOYER_SECRET" -H "Content-Type: application/json" \
        -d "{\"slug\": \"{{slug}}\"}"' | jq .

# Local mode: push examples/hello to the bare repo, build its image, deploy it
hello-local:
    #!/usr/bin/env bash
    set -euo pipefail
    repo=var/git/hello.git
    [[ -d $repo ]] || git init -q --bare --initial-branch=main "$repo"
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
    git clone -q "$repo" "$work" 2>/dev/null
    cp -R examples/hello/. "$work"/
    sed -i.bak "s|ghcr.io/your-org/|ghcr.io/$GITHUB_ORG/|" "$work/deploy/compose.yml" && rm "$work/deploy/compose.yml.bak"
    git -C "$work" add -A
    git -C "$work" -c user.name=golem -c user.email=golem@localhost commit -qm "Update hello" || true
    git -C "$work" push -q origin HEAD:main
    sha=$(git -C "$work" rev-parse HEAD)
    # What CI does on GitHub: an image tagged with the commit.
    docker build -q -t "ghcr.io/$GITHUB_ORG/hello:$sha" examples/hello >/dev/null
    just deploy hello "$sha"

# Delete every project and start from zero (says what will go, asks first)
nuke:
    bin/nuke.sh

# Build and start the stack
up:
    docker compose up -d --build

# Stop the stack, keeping volumes
down:
    docker compose down

# Show service state
ps:
    docker compose ps

# Follow a service's logs
logs svc:
    docker compose logs -f {{svc}}
