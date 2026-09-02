# syntax=docker/dockerfile:1.4
# Neutral shared MoonMind Omnigent host image for Codex, Claude Code, and OpenCode.
# Derives from the same immutable Omnigent host base used by MoonMind.
# Adds pinned Codex, Claude Code, and OpenCode CLIs at build time — never at workflow launch.
#
# Build args:
#   OMNIGENT_HOST_BASE_IMAGE - pinned base, e.g. ghcr.io/omnigent-ai/omnigent-host@sha256:<digest>
#   OMNIGENT_BUILD_DIGEST     - portable build identity sha256
#   OPENCODE_VERSION, CODEX_VERSION, CLAUDE_VERSION - exact pinned versions
#
# See docs/Omnigent/OpenCodeHost.md and docs/Omnigent/PrimaryRuntimeProviderStrategy.md
# sections 5.2, 7, 10 for contract.

ARG OMNIGENT_HOST_BASE_IMAGE
ARG OMNIGENT_BUILD_DIGEST
ARG OPENCODE_VERSION=1.18.11
ARG OPENCODE_MIN_VERSION=1.17.7
ARG OPENCODE_MAX_VERSION=1.19.0
ARG CODEX_VERSION=0.52.0
ARG CODEX_MIN_VERSION=0.50.0
ARG CODEX_MAX_VERSION=1.0.0
ARG CLAUDE_VERSION=2.1.257
ARG CLAUDE_MIN_VERSION=2.0.0
ARG CLAUDE_MAX_VERSION=3.0.0

FROM ${OMNIGENT_HOST_BASE_IMAGE} AS base

ARG OPENCODE_VERSION
ARG OMNIGENT_BUILD_DIGEST
ARG OPENCODE_MIN_VERSION
ARG OPENCODE_MAX_VERSION
ARG CODEX_VERSION
ARG CODEX_MIN_VERSION
ARG CODEX_MAX_VERSION
ARG CLAUDE_VERSION
ARG CLAUDE_MIN_VERSION
ARG CLAUDE_MAX_VERSION

SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]

USER root

RUN [[ "${OMNIGENT_BUILD_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || { echo "OMNIGENT_BUILD_DIGEST must be an exact sha256 build identity" >&2; exit 1; }

# Install all required runtimes at build time only.
# Workflow launch must never run npm install.
RUN npm config set fund false \
    && npm config set audit false \
    && npm config set update-notifier false \
    && echo "Installing pinned runtimes: opencode-ai@${OPENCODE_VERSION} @openai/codex@${CODEX_VERSION} @anthropic-ai/claude-code@${CLAUDE_VERSION}" \
    && npm install -g --no-audit --no-fund --include=optional "opencode-ai@${OPENCODE_VERSION}" "@openai/codex@${CODEX_VERSION}" "@anthropic-ai/claude-code@${CLAUDE_VERSION}" \
    && echo "Verifying runtime installations..." \
    && command -v opencode >/dev/null || { echo "opencode binary missing after install" >&2; exit 1; } \
    && command -v codex >/dev/null || { echo "codex binary missing after install" >&2; exit 1; } \
    && command -v claude >/dev/null || { echo "claude binary missing after install" >&2; exit 1; } \
    && command -v omnigent >/dev/null || { echo "omnigent binary missing (must be in base)" >&2; exit 1; } \
    && INSTALLED_OPENCODE="$(opencode --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)" \
    && echo "Installed opencode version: ${INSTALLED_OPENCODE}" \
    && if [ -z "${INSTALLED_OPENCODE}" ]; then echo "Could not parse opencode --version" >&2; exit 1; fi \
    && python3 -c "import sys; from packaging.version import Version; iv=Version('${INSTALLED_OPENCODE}'); minv=Version('${OPENCODE_MIN_VERSION}'); maxv=Version('${OPENCODE_MAX_VERSION}'); sys.exit(0 if minv <= iv < maxv else 1)" \
        || { echo "opencode version ${INSTALLED_OPENCODE} outside supported range ${OPENCODE_MIN_VERSION}..${OPENCODE_MAX_VERSION}" >&2; exit 1; } \
    && INSTALLED_CODEX="$(codex --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)" \
    && echo "Installed codex version: ${INSTALLED_CODEX}" \
    && if [ -z "${INSTALLED_CODEX}" ]; then INSTALLED_CODEX="${CODEX_VERSION}"; echo "Could not parse codex --version, using pinned ${CODEX_VERSION}"; fi \
    && python3 -c "import sys; from packaging.version import Version; iv=Version('${INSTALLED_CODEX}'); minv=Version('${CODEX_MIN_VERSION}'); maxv=Version('${CODEX_MAX_VERSION}'); sys.exit(0 if minv <= iv < maxv else 1)" \
        || { echo "codex version ${INSTALLED_CODEX} outside supported range ${CODEX_MIN_VERSION}..${CODEX_MAX_VERSION}" >&2; exit 1; } \
    && INSTALLED_CLAUDE="$(claude --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)" \
    && echo "Installed claude version: ${INSTALLED_CLAUDE}" \
    && if [ -z "${INSTALLED_CLAUDE}" ]; then INSTALLED_CLAUDE="${CLAUDE_VERSION}"; echo "Could not parse claude --version, using pinned ${CLAUDE_VERSION}"; fi \
    && python3 -c "import sys; from packaging.version import Version; iv=Version('${INSTALLED_CLAUDE}'); minv=Version('${CLAUDE_MIN_VERSION}'); maxv=Version('${CLAUDE_MAX_VERSION}'); sys.exit(0 if minv <= iv < maxv else 1)" \
        || { echo "claude version ${INSTALLED_CLAUDE} outside supported range ${CLAUDE_MIN_VERSION}..${CLAUDE_MAX_VERSION}" >&2; exit 1; } \
    && echo "Probing exact binaries:" \
    && omnigent --version \
    && codex --version \
    && claude --version \
    && opencode --version \
    && (npm cache clean --force || true) \
    && echo "shared host image ready"

# Ensure forbidden secrets not embedded and no ambient provider credentials
RUN echo "Scanning for embedded credentials and forbidden env defaults..." \
    && if env | grep -qE '^(OPENAI_API_KEY|ANTHROPIC_API_KEY|CLAUDE_API_KEY|OPENCODE_API_KEY|GH_TOKEN|GITHUB_TOKEN)='; then echo "Forbidden provider credential env embedded" >&2; exit 1; fi \
    && if [ -f /home/app/.codex/auth.json ] || [ -f /home/app/.claude/.credentials.json ] || [ -f /home/app/.local/share/opencode/auth.json ]; then echo "Provider credential file embedded" >&2; exit 1; fi \
    && echo "No embedded credentials found"

# The exact-host launcher owns the portable numeric runtime contract.
RUN if ! getent group 1000 >/dev/null; then groupadd --gid 1000 app; fi \
    && if ! getent passwd 1000 >/dev/null; then useradd --uid 1000 --gid 1000 --home-dir /home/app --shell /bin/sh app; fi \
    && mkdir -p /home/app \
    && chown 1000:1000 /home/app

USER 1000:1000
WORKDIR /home/app

ENV HOME="/home/app" \
    PATH="/usr/local/bin:${PATH}"

# Sanity check as runtime user: PATH resolution, uid/gid, home
RUN echo "Runtime user check: $(id -u):$(id -g) home=${HOME}" \
    && [ "$(id -u)" = "1000" ] || { echo "uid must be 1000" >&2; exit 1; } \
    && [ "$(id -g)" = "1000" ] || { echo "gid must be 1000" >&2; exit 1; } \
    && [ "${HOME}" = "/home/app" ] || { echo "HOME must be /home/app" >&2; exit 1; } \
    && command -v omnigent && omnigent --version \
    && command -v codex && codex --version \
    && command -v claude && claude --version \
    && command -v opencode && opencode --version \
    && echo "All runtimes resolvable in PATH for uid 1000"

LABEL org.opencontainers.image.title="omnigent-host-moonmind" \
      org.opencontainers.image.description="Neutral shared MoonMind Omnigent host with Codex, Claude Code, OpenCode pinned at build time" \
      org.opencontainers.image.source="https://github.com/MoonLadderStudios/MoonMind" \
      moonmind.omnigent.build_digest="${OMNIGENT_BUILD_DIGEST}" \
      moonmind.opencode.version="${OPENCODE_VERSION}" \
      moonmind.codex.version="${CODEX_VERSION}" \
      moonmind.claude.version="${CLAUDE_VERSION}"
