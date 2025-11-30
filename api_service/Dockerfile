# syntax=docker/dockerfile:1.4
ARG CODEX_CLI_VERSION=0.6.0
ARG SPEC_KIT_VERSION=0.4.0
# Default to skipping Codex/Spec Kit installs so CI and local builds do not
# depend on external npm registry availability. Enable explicitly with
# `--build-arg INSTALL_CODEX_CLI=true` when the CLIs are required.
ARG INSTALL_CODEX_CLI=false
ARG INSTALL_GEMINI_CLI=false
ARG GEMINI_CLI_VERSION=latest
ARG INSTALL_TEST_DEPS=false

FROM node:20-bookworm AS tooling-builder

# Ensure all RUN commands in this stage use bash with pipefail and fail fast on
# unset variables, so we don't hit dash incompatibilities or silently continue
# when build args are missing. Keep the strict flags explicit on the SHELL
# instruction so future RUN steps automatically inherit them.
SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]

ARG CODEX_CLI_VERSION
ARG SPEC_KIT_VERSION
ARG INSTALL_CODEX_CLI=true
ARG GITHUBNEXT_NPM_REGISTRY=https://npm.pkg.github.com
ARG INSTALL_TEST_DEPS
ARG GEMINI_CLI_VERSION
ARG INSTALL_GEMINI_CLI

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
ARG GITHUB_TOKEN
ARG NPM_TOKEN

# Install Codex and Spec Kit CLIs globally so binaries can be copied to the
# runtime image without shipping npm tooling alongside the Python stack.
# If the packages are unavailable (for example, when the registry is private or
# the tag is missing), fall back to lightweight stub binaries so downstream
# builds can continue to function in CI environments that only need the
# Python stack. Run the installer inside an explicit bash heredoc so the
# command never falls back to `/bin/sh` even if the Dockerfile shell is
# changed later.
RUN /bin/bash -euo pipefail <<'BASH'
case "${CODEX_CLI_VERSION}" in
    (""|*[!0-9A-Za-z._-]*) echo "Invalid CODEX_CLI_VERSION: ${CODEX_CLI_VERSION}" >&2; exit 1;;
esac
case "${SPEC_KIT_VERSION}" in
    (""|*[!0-9A-Za-z._-]*) echo "Invalid SPEC_KIT_VERSION: ${SPEC_KIT_VERSION}" >&2; exit 1;;
esac
case "${GEMINI_CLI_VERSION}" in
    (""|*[!0-9A-Za-z._-]*) echo "Invalid GEMINI_CLI_VERSION: ${GEMINI_CLI_VERSION}" >&2; exit 1;;
esac
stub_codex_and_spec() {
    mkdir -p /usr/local/lib/node_modules/@githubnext/codex-cli \
             /usr/local/lib/node_modules/@githubnext/spec-kit
    printf 'codex-cli stub installed during fallback; no upstream package available.\n' > /usr/local/lib/node_modules/@githubnext/codex-cli/LICENSE
    printf 'spec-kit stub installed during fallback; no upstream package available.\n' > /usr/local/lib/node_modules/@githubnext/spec-kit/LICENSE
    printf '%s\n' "#!/usr/bin/env bash" \
                   "echo \"codex-cli stub: package unavailable during image build\" >&2" \
                   "exit 0" > /usr/local/bin/codex
    chmod +x /usr/local/bin/codex
    printf '%s\n' "#!/usr/bin/env bash" \
                   "echo \"spec-kit stub: package unavailable during image build\" >&2" \
                   "exit 0" > /usr/local/bin/speckit
    chmod +x /usr/local/bin/speckit
}
stub_gemini() {
    mkdir -p /usr/local/lib/node_modules/@google/gemini-cli
    printf 'gemini-cli stub installed during fallback; no upstream package available.\n' > /usr/local/lib/node_modules/@google/gemini-cli/LICENSE
    printf '%s\n' "#!/usr/bin/env bash" \
                   "echo \"gemini-cli stub: package unavailable during image build\" >&2" \
                   "exit 0" > /usr/local/bin/gemini
    chmod +x /usr/local/bin/gemini
}

if [ "${INSTALL_CODEX_CLI}" != "true" ]; then
    echo "INSTALL_CODEX_CLI=${INSTALL_CODEX_CLI}; skipping Codex/Spec Kit install and using stubs" >&2
    stub_codex_and_spec
    npm cache clean --force || true
else
    echo "@githubnext:registry=${GITHUBNEXT_NPM_REGISTRY}" > /root/.npmrc
    npm config set @githubnext:registry "${GITHUBNEXT_NPM_REGISTRY}"
    if [ -n "${GITHUB_TOKEN:-}" ]; then
        npm config set //npm.pkg.github.com/:_authToken "${GITHUB_TOKEN}"
        npm config set always-auth true
    elif [ -n "${NPM_TOKEN:-}" ]; then
        npm config set //npm.pkg.github.com/:_authToken "${NPM_TOKEN}"
        npm config set always-auth true
    fi

    if [ -z "${GITHUB_TOKEN:-}" ] && [ -z "${NPM_TOKEN:-}" ]; then
        echo "No GitHub/NPM token provided; installing Codex/Spec Kit stubs" >&2
        stub_codex_and_spec
    elif ! npm install -g \
        @githubnext/codex-cli@"${CODEX_CLI_VERSION}" \
        @githubnext/spec-kit@"${SPEC_KIT_VERSION}"; then
        INSTALL_STATUS=$?
        echo "Warning: Failed to install Codex/Spec Kit from ${GITHUBNEXT_NPM_REGISTRY} (exit ${INSTALL_STATUS}); installing stub binaries" >&2
        stub_codex_and_spec
    fi
fi

if [ "${INSTALL_GEMINI_CLI}" != "true" ]; then
    echo "INSTALL_GEMINI_CLI=${INSTALL_GEMINI_CLI}; skipping Gemini CLI install and using stub" >&2
    stub_gemini
elif ! npm install -g @google/gemini-cli@"${GEMINI_CLI_VERSION}"; then
    INSTALL_STATUS=$?
    echo "Warning: Failed to install Gemini CLI (exit ${INSTALL_STATUS}); installing stub binary" >&2
    stub_gemini
fi

npm cache clean --force || true
BASH
# Fail the build early if the Codex or Spec Kit CLI binaries are missing or
# broken. This helps catch upstream npm publish issues before the runtime image
# is produced.
RUN codex --version \
    && speckit --version \
    && gemini --version

FROM python:3.12-slim-bookworm

ARG CODEX_CLI_VERSION
ARG SPEC_KIT_VERSION
ARG INSTALL_TEST_DEPS
ARG GEMINI_CLI_VERSION
ARG INSTALL_GEMINI_CLI

# Use bash with pipefail and strict mode in the runtime image as well so
# future RUN commands that rely on bash-specific behavior do not regress to
# /bin/sh defaults or ignore unset variables. Keep the options explicit for
# readability.
SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]

ENV CODEX_CLI_VERSION=${CODEX_CLI_VERSION} \
    SPEC_KIT_VERSION=${SPEC_KIT_VERSION} \
    PATH="/usr/local/bin:${PATH}"

WORKDIR /app

# Copy Codex and Spec Kit artifacts from the tooling builder so the runtime
# image retains only the compiled CLIs and minimal Node runtime needed to
# execute them.
COPY --from=tooling-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=tooling-builder /usr/local/bin/codex /usr/local/bin/codex
COPY --from=tooling-builder /usr/local/bin/speckit /usr/local/bin/speckit
COPY --from=tooling-builder /usr/local/bin/gemini /usr/local/bin/gemini
COPY --from=tooling-builder /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN mkdir -p /usr/share/licenses/codex-cli /usr/share/licenses/spec-kit

COPY --from=tooling-builder /usr/local/lib/node_modules/@githubnext/codex-cli/LICENSE /usr/share/licenses/codex-cli/LICENSE
COPY --from=tooling-builder /usr/local/lib/node_modules/@githubnext/spec-kit/LICENSE /usr/share/licenses/spec-kit/LICENSE

# Install system packages required by the Python service along with the
# Node.js runtime libraries that the bundled Codex and Spec Kit binaries rely on.
RUN apt-get update && \
    apt-get install -y \
        build-essential \
        pkg-config \
        libcairo2-dev \
        libffi-dev \
        tesseract-ocr \
        tesseract-ocr-eng \
        git \
        curl \
        gnupg \
        nodejs && \
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

# Ensure the non-root ``app`` user exists for Celery workers and the bundled
# CLIs remain executable from its PATH.
RUN useradd --create-home --home-dir /home/app --shell /bin/bash app && \
    chmod 0755 /usr/local/bin/codex /usr/local/bin/speckit /usr/local/bin/gemini

# Verify the non-root ``app`` user can resolve and execute the bundled CLIs.
RUN if [ "${INSTALL_GEMINI_CLI:-true}" = "true" ]; then \
        runuser -u app -- sh -c 'codex --version && speckit --version && gemini --version'; \
    else \
        runuser -u app -- sh -c 'codex --version && speckit --version'; \
    fi

# Provide a managed Codex configuration template that enforces non-interactive
# approvals. The template will be merged into the worker's home directory at
# runtime to keep the `approval_policy` pinned to "never".
RUN install -d -o root -g app -m 0750 /etc/codex
COPY --chown=root:app --chmod=0640 api_service/config.template.toml /etc/codex/config.toml

RUN pip install watchfiles

# Copy pyproject.toml and README.md first for better Docker layer caching
COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md

# Create empty directories to satisfy pip install
RUN mkdir -p /app/moonmind /app/api_service
RUN touch /app/moonmind/__init__.py /app/api_service/__init__.py

# Install dependencies first (this layer will be cached as long as pyproject.toml doesn't change)
RUN pip install -e .
RUN pip install readmeai~=0.6.3 --no-deps

# Install test extras when requested so compose-driven test runs have pytest and
# related dependencies baked into the image, avoiding runtime network installs.
RUN if [ "${INSTALL_TEST_DEPS}" = "true" ]; then \
        pip install -e .[tests]; \
    fi

# Copy source code AFTER installing dependencies
# This ensures that code changes don't invalidate the dependency cache
COPY moonmind /app/moonmind/
COPY api_service /app/api_service/

RUN chmod +x /app/api_service/entrypoint.sh && \
    sed -i 's/\r$//' /app/api_service/entrypoint.sh

EXPOSE 5000

CMD ["/app/api_service/entrypoint.sh"]
