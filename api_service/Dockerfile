# syntax=docker/dockerfile:1.4
FROM ghcr.io/wollomatic/socket-proxy:1.13.2 AS docker-proxy-upstream

# A small host-test target shares the exact proxy payload with release images.
FROM python:3.15-slim-bookworm AS docker-proxy-runtime
WORKDIR /app
COPY --from=docker-proxy-upstream /socket-proxy /usr/local/bin/moonmind-socket-proxy
COPY docker/docker-proxy/start.py /app/docker/docker-proxy/start.py
COPY docker/docker-proxy/LICENSE.socket-proxy /usr/share/licenses/moonmind-socket-proxy/LICENSE
ENTRYPOINT ["python", "/app/docker/docker-proxy/start.py"]

# ---------------------------------------------------------------------------
# Frontend builder – compiles Tailwind CSS and Vite assets in a Node environment
# so the slim Python runtime image does not need npm.
# ---------------------------------------------------------------------------
FROM node:26-bookworm AS node-runtime

# Retained Temporal managed launches and their retries still execute these
# CLIs in the shared API/worker image. Remove this stage only after those
# consumers migrate to Omnigent and their existing work has drained.
FROM node-runtime AS managed-tooling
ARG CODEX_CLI_VERSION=latest
ARG CLAUDE_CLI_VERSION=2.1.281
RUN --mount=type=cache,target=/root/.npm \
    npm install --global --prefix /opt/moonmind-managed-cli --include=optional --no-audit --no-fund \
        "@openai/codex@${CODEX_CLI_VERSION}" \
        "@anthropic-ai/claude-code@${CLAUDE_CLI_VERSION}" \
    && /opt/moonmind-managed-cli/bin/codex --version \
    && /opt/moonmind-managed-cli/bin/claude --version

FROM node-runtime AS frontend-builder
SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]
WORKDIR /build

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    apt-get update && apt-get install -y --no-install-recommends python3
RUN npm config set fund false \
    && npm config set audit false \
    && npm config set update-notifier false

COPY package.json package-lock.json /build/
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-fund --no-audit

# Copy only the source files needed for the frontend build after dependencies
# are installed so source-only edits do not invalidate npm ci.
COPY postcss.config.cjs tailwind.config.cjs /build/
COPY frontend/ /build/frontend/
# Tailwind scans these paths for class usage (see tailwind.config.cjs content[])
COPY api_service/templates/react_dashboard.html /build/api_service/templates/react_dashboard.html
COPY tools/verify_vite_manifest.py /build/tools/verify_vite_manifest.py

# Always rebuild dist from source; do not rely on committed chunks surviving partial copies.
RUN rm -rf /build/api_service/static/workflow_console/dist \
    && npm run ui:build \
    && python3 /build/tools/verify_vite_manifest.py

# ---------------------------------------------------------------------------
FROM python:3.15-slim-bookworm AS python-requirements
SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]
WORKDIR /deps

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --disable-pip-version-check \
        poetry==2.4.1 \
        poetry-plugin-export==1.10.0

COPY pyproject.toml poetry.lock README.md /deps/

RUN poetry export --only main --format requirements.txt --without-hashes --output /deps/requirements-main.txt \
    && poetry export --only main --extras tests --format requirements.txt --without-hashes --output /deps/requirements-tests.txt

# ---------------------------------------------------------------------------
FROM python:3.15-slim-bookworm AS runtime-dependencies

# Use bash with pipefail and strict mode in the runtime image as well so
# future RUN commands that rely on bash-specific behavior do not regress to
# /bin/sh defaults or ignore unset variables. Keep the options explicit for
# readability.
SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]

WORKDIR /app

# Keep the shared Python/test layer free of agent tooling. The production
# layer retains the CLIs required by pre-Omnigent Temporal launch consumers.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ripgrep \
        git \
        gh \
        curl \
        ca-certificates \
        openssh-client \
        procps && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        docker-buildx-plugin \
        docker-ce-cli \
        docker-compose-plugin

RUN install -d -m 0755 /usr/libexec/moonmind && \
    mv /usr/bin/docker /usr/libexec/moonmind/docker-real
COPY api_service/docker/moonmind-docker-wrapper.sh /usr/bin/docker
RUN chmod 0755 /usr/bin/docker /usr/libexec/moonmind/docker-real

# Temporal workers and managed container jobs use this non-root identity.
RUN useradd --create-home --home-dir /home/app --shell /bin/bash app

# Verify the worker's diagnostic and container-job tools remain executable.
RUN runuser -u app -- env \
    HOME=/home/app \
    PATH="/usr/local/bin:/usr/local/sbin:/usr/bin:/bin" \
    sh -c 'rg --version && docker buildx version && docker compose version'

COPY --from=python-requirements /deps/requirements-main.txt /tmp/requirements-main.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --disable-pip-version-check -r /tmp/requirements-main.txt \
    && rm -f /tmp/requirements-main.txt

COPY pyproject.toml /app/pyproject.toml
COPY poetry.lock /app/poetry.lock
COPY package.json package-lock.json /app/
COPY README.md /app/README.md
COPY LICENSE /app/LICENSE
COPY NOTICE /app/NOTICE
COPY --from=docker-proxy-runtime /usr/local/bin/moonmind-socket-proxy /usr/local/bin/moonmind-socket-proxy
COPY --from=docker-proxy-runtime /app/docker/docker-proxy/start.py /app/docker/docker-proxy/start.py
COPY --from=docker-proxy-runtime /usr/share/licenses/moonmind-socket-proxy /usr/share/licenses/moonmind-socket-proxy

# Keep the test image buildable from the shallow, submodule-free checkout used
# by required backend CI and managed workflow workspaces. Tests execute the
# mounted checkout via PYTHONPATH, so this stage needs locked dependencies but
# must not inherit production-only source COPY steps such as Omnigent.
FROM runtime-dependencies AS test-runtime
ARG INSTALL_TEST_DEPS=true
# The local test wrapper supports frontend tests. Node/npm belong only in this
# target; production receives the compiled frontend assets instead.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=python-requirements /deps/requirements-tests.txt /tmp/requirements-tests.txt
COPY --chown=app:app api_service/docker/cache_temporal_test_server.py /tmp/cache_temporal_test_server.py
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    apt-get update && \
    apt-get install -y --no-install-recommends postgresql libatomic1 && \
    ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    runuser -u app -- node --version && runuser -u app -- npm --version
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "${INSTALL_TEST_DEPS}" = "true" ]; then \
        pip install --disable-pip-version-check -r /tmp/requirements-tests.txt; \
    fi && \
    rm -f /tmp/requirements-tests.txt
ENV HOME=/home/app
USER app
RUN if [ "${INSTALL_TEST_DEPS}" = "true" ]; then \
        python /tmp/cache_temporal_test_server.py; \
    fi && \
    rm -f /tmp/cache_temporal_test_server.py

FROM runtime-dependencies AS runtime-base

# Install the egress proxy and the retained CLIs' Node runtime before source
# copies so source edits retain this layer. npm stays in the build/test images.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends squid libatomic1
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=managed-tooling /opt/moonmind-managed-cli /opt/moonmind-managed-cli
RUN ln -s /opt/moonmind-managed-cli/bin/codex /usr/local/bin/codex \
    && ln -s /opt/moonmind-managed-cli/bin/claude /usr/local/bin/claude \
    && runuser -u app -- env HOME=/home/app \
        PATH="/usr/local/bin:/usr/local/sbin:/usr/bin:/bin" \
        sh -c 'codex --version && claude --version'

# Copy source code AFTER installing locked dependencies so code changes only
# invalidate the lightweight project install.
COPY moonmind /app/moonmind/
# The existing host-owned bootstrap can run this small stdlib controller
# from the already-published application image before an update submission.
COPY deploy/controller /app/deploy/controller/
COPY docker/sandbox-egress-proxy /app/docker/sandbox-egress-proxy/
# The enforcer's own policy is image-owned and lives in its own source
# directory. It must never be copied out of the deployment-mounted directory:
# a live v1 gateway bind-mounts that directory's squid.conf, so rewriting it
# for a newer enforcer invalidates every running v1 gateway on the host.
COPY docker/moonmind-egress /opt/moonmind-egress/
# The embedded host-auth adapter executes verifier sources from the exact
# pinned submodule revision. Keep that immutable source available in every API
# and worker runtime derived from this stage.
COPY omnigent/omnigent /app/omnigent/omnigent/
COPY pr_resolver_core /app/pr_resolver_core/
COPY services/omnigent/tools/manifest.lock.json /app/services/omnigent/tools/manifest.lock.json
COPY services/omnigent/profile/moonmind-tools.sh /app/services/omnigent/profile/moonmind-tools.sh
COPY services/omnigent/opencode-host/verify-warm-plugin-cache.sh /app/services/omnigent/opencode-host/verify-warm-plugin-cache.sh
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --disable-pip-version-check --no-deps .
COPY api_service /app/api_service/
COPY config /app/config/
COPY .agents /app/.agents/
COPY services /app/services/
COPY docs /app/docs/
COPY tools/verify_deployed_ui_assets.py /app/tools/verify_deployed_ui_assets.py
COPY init_db /app/init_db/
COPY docker-compose.yaml /app/release/docker-compose.yaml

# Copy pre-built frontend assets from the frontend-builder stage
COPY --from=frontend-builder /build/api_service/static/workflow_console/ /app/api_service/static/workflow_console/
COPY --from=frontend-builder /build/api_service/static/workflow_console/ /opt/moonmind_static/workflow_console/

RUN chmod +x /app/api_service/entrypoint.sh && \
    sed -i 's/\r$//' /app/api_service/entrypoint.sh

FROM runtime-base AS worker-runtime
ARG MOONMIND_BUILD_SHA
LABEL org.opencontainers.image.revision="${MOONMIND_BUILD_SHA}"
RUN python /app/moonmind/release_identity.py --root /app --revision "${MOONMIND_BUILD_SHA:-}"
EXPOSE 8000
CMD ["/app/api_service/entrypoint.sh"]

FROM worker-runtime AS api-runtime
ARG MOONMIND_BUILD_ID
RUN build_id="${MOONMIND_BUILD_ID:-}" \
    && if [ -z "${build_id}" ]; then build_id="$(date -u +%Y%m%d.%H%M)"; fi \
    && printf '%s\n' "${build_id}" > /app/.moonmind-build-id
# Stamp the same build id used by the workflow-header version badge as the
# canonical OCI image version label so deployment updates can read the
# deployed build id directly from `docker image inspect` on the target image.
LABEL org.opencontainers.image.version="${MOONMIND_BUILD_ID}"

EXPOSE 8000

CMD ["/app/api_service/entrypoint.sh"]
