# The golem service and the worker image: Claude Code workers run as
# subprocesses in here, so this container is the sandbox.
# Build context is the repo root (process files are baked in).
FROM node:24-slim AS node
FROM ghcr.io/astral-sh/uv:0.12 AS uv

FROM python:3.13-slim
ARG DEBIAN_FRONTEND=noninteractive
# Claude Code from Anthropic's signed apt repo (stable channel; fingerprint
# from https://code.claude.com/docs/en/setup), gh from GitHub's. procps: ps, pgrep and pkill,
# which workers reach for to find and stop the dev servers they start (slim images lack them).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git gnupg jq procps ripgrep \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://downloads.claude.ai/keys/claude-code.asc -o /etc/apt/keyrings/claude-code.asc \
    && gpg --show-keys --with-colons /etc/apt/keyrings/claude-code.asc \
        | grep -q '^fpr:::::::::31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE:$' \
    && echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] https://downloads.claude.ai/claude-code/apt/stable stable main" \
        > /etc/apt/sources.list.d/claude-code.list \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends claude-code gh \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=uv /uv /uvx /usr/local/bin/

# Chromium and Playwright, so workers can look at what they build (skills/browser.md).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    NODE_PATH=/usr/local/lib/node_modules
# /node_modules makes `import "playwright"` resolve from any script (ES modules ignore NODE_PATH).
RUN npm install -g playwright@1 \
    && playwright install --with-deps chromium \
    && ln -s /usr/local/lib/node_modules /node_modules \
    && rm -rf /var/lib/apt/lists/* /root/.npm

# OpenCode, the other worker golem can run (golem/opencode.py). Pinned: golem reads its JSON
# events, and a trial's runs are only comparable on a version that holds still.
RUN npm install -g opencode-ai@1.19.6 && opencode --version && rm -rf /root/.npm

# WeasyPrint (HTML to PDF, for projects that print): Chromium already brought Pango, HarfBuzz,
# fontconfig and Liberation fonts; WeasyPrint also loads pangoft2 (178 kB) and subsets embedded
# fonts with harfbuzz-subset (its fontTools fallback is deprecated).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpangoft2-1.0-0 libharfbuzz-subset0 \
    && rm -rf /var/lib/apt/lists/*

# Claude Code refuses to skip permission prompts as root, and workers need that.
RUN useradd --create-home --uid 1000 golem \
    && install -d -o golem -g golem /var/lib/golem /workspace /home/golem/.claude /home/golem/.cache

# Process files: root-owned, so agents can read but not edit them.
COPY workflows /opt/golem/workflows
COPY skills /opt/golem/skills
COPY prompts /opt/golem/prompts
COPY modes /opt/golem/modes

WORKDIR /opt/golem/app
COPY golem/pyproject.toml golem/uv.lock ./
RUN uv sync --frozen --no-dev
COPY golem/golem ./golem
COPY --chmod=755 golem/tools/golem-screenshot.mjs /usr/local/bin/golem-screenshot
COPY --chmod=755 golem/tools/golem-card-shot.mjs /usr/local/bin/golem-card-shot
COPY --chmod=755 golem/tools/golem-render-doc.mjs /usr/local/bin/golem-render-doc
# Workers' commands: `golem` (cli.py: report, deploy...), and `golem-deploy`, their project's
# deployment (skills/deploy.md). -P: a project's own golem/ directory, where the worker runs
# them, mustn't stand in for golem's.
RUN printf '#!/bin/sh\nexec /opt/golem/app/.venv/bin/python -P -m golem.cli "$@"\n' > /usr/local/bin/golem \
    && printf '#!/bin/sh\nexec /opt/golem/app/.venv/bin/python -P -m golem.deploy_cli "$@"\n' > /usr/local/bin/golem-deploy \
    && chmod 755 /usr/local/bin/golem /usr/local/bin/golem-deploy

USER golem
# npm's cache in the cache volume with uv's: each job installs in a fresh tree, from the cache.
ENV PATH="/opt/golem/app/.venv/bin:$PATH" \
    PYTHONPATH=/opt/golem/app \
    PYTHONUNBUFFERED=1 \
    CLAUDE_CONFIG_DIR=/home/golem/.claude \
    DISABLE_AUTOUPDATER=1 \
    npm_config_cache=/home/golem/.cache/npm
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["python", "-m", "golem"]
