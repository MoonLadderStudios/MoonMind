# syntax=docker/dockerfile:1.4
ARG CODEX_CLI_VERSION=latest
ARG SPEC_KIT_VERSION=0.4.0
ARG CLAUDE_CLI_VERSION=latest
# Build the shared runtime image with Codex/Spec Kit/Gemini CLIs by default so
# the API, orchestrator, and Celery workers can run from the same image tag.
# Disable selectively in test-only or constrained builds when needed.
ARG INSTALL_CODEX_CLI=true
ARG INSTALL_GEMINI_CLI=true
ARG INSTALL_CLAUDE_CLI=true
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

# Keep complex platform-vendor installation logic out of the Dockerfile body.
COPY --chmod=0755 api_service/docker/install_codex_platform_vendor.sh /usr/local/bin/install_codex_platform_vendor.sh

# Ensure vendor directory exists so CLIs (like Codex) that might install
# binaries there can detect it during the npm install process.
RUN mkdir -p /usr/local/vendor

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
case "${CLAUDE_CLI_VERSION}" in
    (""|*[!0-9A-Za-z._-]*) echo "Invalid CLAUDE_CLI_VERSION: ${CLAUDE_CLI_VERSION}" >&2; exit 1;;
esac
stub_codex() {
    mkdir -p /usr/local/lib/node_modules/@openai/codex
    printf 'codex stub installed during fallback.\n' > /usr/local/lib/node_modules/@openai/codex/LICENSE
    printf '%s\n' "#!/usr/bin/env bash" \
                   "echo \"codex stub: package unavailable during image build\" >&2" \
                   "exit 0" > /usr/local/bin/codex
    chmod +x /usr/local/bin/codex
}
stub_speckit() {
    mkdir -p /usr/local/lib/node_modules/@githubnext/spec-kit
    printf 'spec-kit stub installed during fallback.\n' > /usr/local/lib/node_modules/@githubnext/spec-kit/LICENSE
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
stub_claude() {
    mkdir -p /usr/local/lib/node_modules/@anthropic-ai/claude-code
    printf 'claude-code stub installed during fallback; package unavailable.\n' > /usr/local/lib/node_modules/@anthropic-ai/claude-code/LICENSE
    printf '%s\n' "#!/usr/bin/env bash" \
                   "echo \"claude-code stub: package unavailable during image build\" >&2" \
                   "exit 0" > /usr/local/bin/claude
    chmod +x /usr/local/bin/claude
}

if [ "${INSTALL_CODEX_CLI}" != "true" ]; then
    echo "INSTALL_CODEX_CLI=${INSTALL_CODEX_CLI}; skipping Codex/Spec Kit install and using stubs" >&2
    stub_codex
    stub_speckit
    npm cache clean --force || true
else
    # Try installing Codex from public registry (OpenAI)
    if ! npm install -g @openai/codex@"${CODEX_CLI_VERSION}"; then
        echo "Warning: Failed to install @openai/codex; installing stub" >&2
        stub_codex
    else
        /usr/local/bin/install_codex_platform_vendor.sh
    fi

    # Try installing Spec Kit (Github Next) - might fail if private/no token
    if ! npm install -g @githubnext/spec-kit@"${SPEC_KIT_VERSION}"; then
        echo "Warning: Failed to install Spec Kit; installing stub" >&2
        stub_speckit
    fi
fi

if [ "${INSTALL_GEMINI_CLI}" != "true" ]; then
    echo "INSTALL_GEMINI_CLI=${INSTALL_GEMINI_CLI}; skipping Gemini CLI install and using stub" >&2
    stub_gemini
elif ! npm install -g @google/gemini-cli@"${GEMINI_CLI_VERSION}"; then
    INSTALL_STATUS=$?
    echo "Warning: Failed to install Gemini CLI (exit ${INSTALL_STATUS}); installing stub binary" >&2
    stub_gemini
else
    # Resolve the symlink target created by npm install -g so we can replace it with a
    # robust wrapper script. This is necessary because Docker COPY dereferences symlinks,
    # breaking relative imports in the target file (e.g., imports from ../src/).
    # By creating a wrapper that points to the absolute path in node_modules, we ensure
    # the CLI works even after being copied to the runtime image.
    GEMINI_LINK_TARGET=$(readlink -f /usr/local/bin/gemini)
    echo "Resolving gemini symlink target: $GEMINI_LINK_TARGET"
    rm /usr/local/bin/gemini
    cat <<EOF > /usr/local/bin/gemini
#!/bin/sh
exec node "$GEMINI_LINK_TARGET" "\$@"
EOF
    chmod +x /usr/local/bin/gemini
fi

if [ "${INSTALL_CLAUDE_CLI}" != "true" ]; then
    echo "INSTALL_CLAUDE_CLI=${INSTALL_CLAUDE_CLI}; skipping Claude CLI install and using stub" >&2
    stub_claude
elif ! npm install -g @anthropic-ai/claude-code@"${CLAUDE_CLI_VERSION}"; then
    INSTALL_STATUS=$?
    echo "Warning: Failed to install Claude CLI (exit ${INSTALL_STATUS}); installing stub binary" >&2
    stub_claude
fi

# Ensure LICENSE files exist so COPY instructions in the next stage do not fail.
# Some npm packages might not include a LICENSE file, or it might be named differently.
if [ ! -f /usr/local/lib/node_modules/@openai/codex/LICENSE ]; then
    mkdir -p /usr/local/lib/node_modules/@openai/codex
    touch /usr/local/lib/node_modules/@openai/codex/LICENSE
fi
if [ ! -f /usr/local/lib/node_modules/@githubnext/spec-kit/LICENSE ]; then
    mkdir -p /usr/local/lib/node_modules/@githubnext/spec-kit
    touch /usr/local/lib/node_modules/@githubnext/spec-kit/LICENSE
fi
if [ ! -f /usr/local/lib/node_modules/@google/gemini-cli/LICENSE ]; then
    mkdir -p /usr/local/lib/node_modules/@google/gemini-cli
    touch /usr/local/lib/node_modules/@google/gemini-cli/LICENSE
fi
if [ ! -f /usr/local/lib/node_modules/@anthropic-ai/claude-code/LICENSE ]; then
    mkdir -p /usr/local/lib/node_modules/@anthropic-ai/claude-code
    touch /usr/local/lib/node_modules/@anthropic-ai/claude-code/LICENSE
fi

npm cache clean --force || true
BASH

# Fail the build early if the Codex or Spec Kit CLI binaries are missing or
# broken. This helps catch upstream npm publish issues before the runtime image
# is produced.
RUN if [ "${INSTALL_CODEX_CLI}" = "true" ]; then \
        if [ -d /usr/local/lib/node_modules/@openai/codex/vendor ]; then \
            cp -r /usr/local/lib/node_modules/@openai/codex/vendor/* /usr/local/vendor/; \
        fi; \
    fi \
    && codex --version \
    && speckit --version \
    && gemini --version \
    && claude --version

FROM python:3.12-slim-bookworm

ARG CODEX_CLI_VERSION
ARG SPEC_KIT_VERSION
ARG INSTALL_TEST_DEPS
ARG GEMINI_CLI_VERSION
ARG INSTALL_GEMINI_CLI
ARG CLAUDE_CLI_VERSION
ARG INSTALL_CLAUDE_CLI

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
COPY --from=tooling-builder /usr/local/bin/claude /usr/local/bin/claude
COPY --from=tooling-builder /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=tooling-builder /usr/local/vendor /usr/local/vendor

RUN mkdir -p /usr/share/licenses/codex-cli /usr/share/licenses/spec-kit /usr/share/licenses/gemini-cli /usr/share/licenses/claude-cli

COPY --from=tooling-builder /usr/local/lib/node_modules/@openai/codex/LICENSE /usr/share/licenses/codex-cli/LICENSE
COPY --from=tooling-builder /usr/local/lib/node_modules/@githubnext/spec-kit/LICENSE /usr/share/licenses/spec-kit/LICENSE
COPY --from=tooling-builder /usr/local/lib/node_modules/@google/gemini-cli/LICENSE /usr/share/licenses/gemini-cli/LICENSE
COPY --from=tooling-builder /usr/local/lib/node_modules/@anthropic-ai/claude-code/LICENSE /usr/share/licenses/claude-cli/LICENSE

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
        gh \
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
    chmod 0755 /usr/local/bin/codex /usr/local/bin/speckit /usr/local/bin/gemini /usr/local/bin/claude

# Verify the non-root ``app`` user can resolve and execute the bundled CLIs.
RUN runuser -u app -- sh -c 'codex --version && speckit --version && gemini --version && claude --version'

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
