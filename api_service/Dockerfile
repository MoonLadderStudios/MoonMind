# syntax=docker/dockerfile:1.4
ARG CODEX_CLI_VERSION=latest
ARG CLAUDE_CLI_VERSION=latest
# Build the shared runtime image with Codex/Gemini/Claude CLIs
# by default so the API and Temporal workers can run from the
# same image tag. Disable selectively in test-only or constrained builds.
ARG INSTALL_CODEX_CLI=true
ARG INSTALL_GEMINI_CLI=true
ARG INSTALL_CLAUDE_CLI=true
ARG GEMINI_CLI_VERSION=latest

FROM node:22-bookworm AS tooling-builder

# Ensure all RUN commands in this stage use bash with pipefail and fail fast on
# unset variables, so we don't hit dash incompatibilities or silently continue
# when build args are missing. Keep the strict flags explicit on the SHELL
# instruction so future RUN steps automatically inherit them.
SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]

ARG CODEX_CLI_VERSION
ARG INSTALL_CODEX_CLI=true
ARG GITHUBNEXT_NPM_REGISTRY=https://npm.pkg.github.com
ARG GEMINI_CLI_VERSION
ARG INSTALL_GEMINI_CLI
ARG CLAUDE_CLI_VERSION
ARG INSTALL_CLAUDE_CLI
# Install build prerequisites required for global npm CLI installs so future
# stages can compile any optional native extensions.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        build-essential && \
    rm -rf /var/lib/apt/lists/*

# Keep npm non-interactive inside Docker builds to avoid analytics prompts.
RUN npm config set fund false \
    && npm config set update-notifier false

ARG GITHUBNEXT_NPM_REGISTRY=https://npm.pkg.github.com
ARG GEMINI_CLI_VERSION
ARG INSTALL_GEMINI_CLI
ARG CLAUDE_CLI_VERSION
ARG INSTALL_CLAUDE_CLI
ARG GITHUB_TOKEN
ARG NPM_TOKEN

# Install Codex, Gemini, and Claude CLIs globally so binaries can be copied to the
# runtime image without shipping npm tooling alongside the Python stack. Keep the
# installer in a normal script file instead of a Dockerfile heredoc so legacy
# Docker builders without buildx execute it consistently.
COPY api_service/docker/install_cli_tooling.sh /tmp/install_cli_tooling.sh
RUN /bin/bash /tmp/install_cli_tooling.sh && rm -f /tmp/install_cli_tooling.sh

# Fail the build early if the bundled CLIs are missing or broken. Pre-create
# ~/.gemini so `gemini --version` can persist registry state.
RUN mkdir -p "${HOME}/.gemini" \
    && codex --version \
    && gemini --version \
    && claude --version

# ---------------------------------------------------------------------------
# Frontend builder – compiles Tailwind CSS and Vite assets in a Node environment
# so the slim Python runtime image does not need npm.
# ---------------------------------------------------------------------------
FROM node:22-bookworm AS frontend-builder
SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
RUN npm config set fund false \
    && npm config set audit false \
    && npm config set update-notifier false

COPY package.json package-lock.json postcss.config.cjs tailwind.config.cjs /build/
RUN npm ci --no-fund --no-audit

# Copy only the source files needed for the frontend build after dependencies
# are installed so source-only edits do not invalidate npm ci.
COPY frontend/ /build/frontend/
# Tailwind scans these paths for class usage (see tailwind.config.cjs content[])
COPY api_service/templates/react_dashboard.html /build/api_service/templates/react_dashboard.html
COPY api_service/templates/_navigation.html /build/api_service/templates/_navigation.html
COPY tools/verify_vite_manifest.py /build/tools/verify_vite_manifest.py

# Always rebuild dist from source; do not rely on committed chunks surviving partial copies.
RUN rm -rf /build/api_service/static/workflow_console/dist \
    && npm run ui:build \
    && python3 /build/tools/verify_vite_manifest.py

# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS python-requirements
SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]
WORKDIR /deps

RUN pip install --disable-pip-version-check \
        poetry==2.2.1 \
        poetry-plugin-export==1.10.0

COPY pyproject.toml poetry.lock README.md /deps/

RUN poetry export --only main --format requirements.txt --without-hashes --output /deps/requirements-main.txt \
    && poetry export --only main --extras tests --format requirements.txt --without-hashes --output /deps/requirements-tests.txt

# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime-base

ARG CODEX_CLI_VERSION
# Use bash with pipefail and strict mode in the runtime image as well so
# future RUN commands that rely on bash-specific behavior do not regress to
# /bin/sh defaults or ignore unset variables. Keep the options explicit for
# readability.
SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]

ENV CODEX_CLI_VERSION=${CODEX_CLI_VERSION} \
    PATH="/usr/local/bin:${PATH}"

WORKDIR /app

# Copy Codex and Gemini/Claude artifacts from the tooling builder so the runtime
# image retains only the compiled CLIs and minimal Node runtime needed to
# execute them.
COPY --from=tooling-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=tooling-builder /usr/local/bin/codex /usr/local/bin/codex
COPY --from=tooling-builder /usr/local/bin/gemini /usr/local/bin/gemini
COPY --from=tooling-builder /usr/local/bin/claude /usr/local/bin/claude
COPY --from=tooling-builder /usr/local/lib/node_modules /usr/local/lib/node_modules

# Docker COPY dereferences the npm/npx symlinks from the Node image. Install
# stable wrappers that keep npm's relative module resolution anchored in the
# copied /usr/local/lib/node_modules tree.
RUN printf '%s\n' \
    '#!/bin/sh' \
    'exec /usr/local/bin/node /usr/local/lib/node_modules/npm/bin/npm-cli.js "$@"' \
    >/usr/local/bin/npm
RUN printf '%s\n' \
    '#!/bin/sh' \
    'exec /usr/local/bin/node /usr/local/lib/node_modules/npm/bin/npx-cli.js "$@"' \
    >/usr/local/bin/npx
RUN chmod 0755 /usr/local/bin/npm /usr/local/bin/npx

RUN mkdir -p /usr/share/licenses/codex-cli /usr/share/licenses/gemini-cli /usr/share/licenses/claude-cli

COPY --from=tooling-builder /usr/local/lib/node_modules/@openai/codex/LICENSE /usr/share/licenses/codex-cli/LICENSE
COPY --from=tooling-builder /usr/local/lib/node_modules/@google/gemini-cli/LICENSE /usr/share/licenses/gemini-cli/LICENSE
COPY --from=tooling-builder /usr/local/lib/node_modules/@anthropic-ai/claude-code/LICENSE /usr/share/licenses/claude-cli/LICENSE

# Install system packages required by the Python service along with the
# Node.js runtime libraries that the bundled CLIs rely on.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bubblewrap \
        libffi-dev \
        ripgrep \
        tesseract-ocr \
        tesseract-ocr-eng \
        git \
        gh \
        curl \
        ca-certificates \
        openssh-client \
        procps \
        gnupg && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        docker-ce-cli \
        docker-compose-plugin && \
    rm -rf /var/lib/apt/lists/*

# Ensure the non-root ``app`` user exists for Temporal workers and the bundled
# CLIs remain executable from its PATH.
# Pre-create ~/.gemini so gemini --version doesn't crash writing projects.json.
# Pre-create ~/.npm as a managed cache so repeated managed-agent runs do not
# pay the full install cost every time.
RUN useradd --create-home --home-dir /home/app --shell /bin/bash app && \
    chmod 0755 /usr/local/bin/codex /usr/local/bin/gemini /usr/local/bin/claude /usr/local/bin/npm /usr/local/bin/npx && \
    mkdir -p /home/app/.gemini && chown app:app /home/app/.gemini && \
    mkdir -p /home/app/.npm && chown app:app /home/app/.npm

# Verify the non-root ``app`` user can resolve and execute the bundled CLIs and
# repository search tooling expected by Codex runtime defaults.
RUN runuser -u app -- env -u NODE_OPTIONS -u npm_config_node_options \
    HOME=/home/app \
    PATH="/usr/local/bin:/usr/local/sbin:/usr/bin:/bin" \
    sh -c 'codex --version && gemini --version && claude --version && npm --version && rg --version'

# Provide a managed Codex configuration template that enforces non-interactive
# approvals and managed sandbox defaults. The template is merged into the
# worker's home directory at runtime.
RUN install -d -o root -g app -m 0750 /etc/codex
COPY api_service/config.template.toml /etc/codex/config.toml
RUN chown root:app /etc/codex/config.toml && chmod 0640 /etc/codex/config.toml

COPY --from=python-requirements /deps/requirements-main.txt /tmp/requirements-main.txt
RUN pip install --disable-pip-version-check -r /tmp/requirements-main.txt \
    && rm -f /tmp/requirements-main.txt

COPY pyproject.toml /app/pyproject.toml
COPY poetry.lock /app/poetry.lock
COPY README.md /app/README.md

RUN pip install --disable-pip-version-check readmeai~=0.6.3 --no-deps

# Copy source code AFTER installing locked dependencies so code changes only
# invalidate the lightweight project install.
COPY moonmind /app/moonmind/
COPY api_service /app/api_service/
COPY config /app/config/
COPY .agents /app/.agents/
RUN pip install --disable-pip-version-check --no-deps .

# Copy pre-built frontend assets from the frontend-builder stage
COPY --from=frontend-builder /build/api_service/static/workflow_console/ /app/api_service/static/workflow_console/
COPY --from=frontend-builder /build/api_service/static/workflow_console/ /opt/moonmind_static/workflow_console/

RUN chmod +x /app/api_service/entrypoint.sh && \
    sed -i 's/\r$//' /app/api_service/entrypoint.sh

FROM runtime-base AS test-runtime
ARG INSTALL_TEST_DEPS=true
COPY --from=python-requirements /deps/requirements-tests.txt /tmp/requirements-tests.txt
RUN if [ "${INSTALL_TEST_DEPS}" = "true" ]; then \
        pip install --disable-pip-version-check -r /tmp/requirements-tests.txt; \
    fi && \
    rm -f /tmp/requirements-tests.txt

FROM runtime-base AS worker-runtime
EXPOSE 8000
CMD ["/app/api_service/entrypoint.sh"]

FROM worker-runtime AS api-runtime
ARG MOONMIND_BUILD_ID
RUN build_id="${MOONMIND_BUILD_ID:-}" \
    && if [ -z "${build_id}" ]; then build_id="$(date -u +%Y%m%d.%H%M)"; fi \
    && printf '%s\n' "${build_id}" > /app/.moonmind-build-id

EXPOSE 8000

CMD ["/app/api_service/entrypoint.sh"]
