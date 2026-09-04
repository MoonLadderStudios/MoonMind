# syntax=docker/dockerfile:1.4
# Neutral shared MoonMind Omnigent host image.
#
# One digest-pinned image carries every approved vendor runtime (Codex CLI,
# Claude Code CLI, OpenCode CLI) on the immutable Omnigent host base. Host
# Classes keep support exact: separate Codex, Claude Code, and OpenCode Host
# Classes reference this same digest while declaring only their own harness,
# runtime pack, and credential materializers. An installed CLI never implies a
# supported runtime — exact-host attestation and runtime-pack probes do.
#
# Required build args:
#   OMNIGENT_HOST_BASE_IMAGE - pinned Omnigent host base, e.g.
#                              ghcr.io/omnigent-ai/omnigent-host@sha256:<digest>
#   OMNIGENT_BUILD_DIGEST    - exact sha256 build identity shared with the
#                              Omnigent server for this release pairing
#
# Vendor pins: each CLI is installed at build time only; the workflow launcher
# must never install runtimes. The pinned versions below mirror the trusted
# runtime-pack descriptors in
# moonmind/omnigent/harness_platform/runtime_packs.py and the deployment CLI
# pins (CLAUDE_CLI_VERSION in api_service/Dockerfile, the documented known-good
# CODEX_CLI_VERSION in tools/update-moonmind.sh, OPENCODE_PINNED_VERSION in
# host_classes.py). Update image pins and pack descriptors in the same change;
# exact-host attestation rejects a drifted runtime.

ARG OMNIGENT_HOST_BASE_IMAGE
ARG OMNIGENT_BUILD_DIGEST
ARG CODEX_CLI_VERSION=0.104.0
ARG CODEX_MIN_VERSION=0.100.0
ARG CODEX_MAX_VERSION=0.200.0
ARG CLAUDE_CLI_VERSION=2.1.257
ARG CLAUDE_MIN_VERSION=2.0.0
ARG CLAUDE_MAX_VERSION=3.0.0
ARG OPENCODE_VERSION=1.18.11
ARG OPENCODE_MIN_VERSION=1.17.7
ARG OPENCODE_MAX_VERSION=1.19.0
ARG OPENCODE_PLUGIN_NPM_CACHE=/opt/moonmind/opencode-npm-cache

FROM ${OMNIGENT_HOST_BASE_IMAGE} AS base

# Re-declare args after FROM
ARG OMNIGENT_BUILD_DIGEST
ARG CODEX_CLI_VERSION
ARG CODEX_MIN_VERSION
ARG CODEX_MAX_VERSION
ARG CLAUDE_CLI_VERSION
ARG CLAUDE_MIN_VERSION
ARG CLAUDE_MAX_VERSION
ARG OPENCODE_VERSION
ARG OPENCODE_MIN_VERSION
ARG OPENCODE_MAX_VERSION
ARG OPENCODE_PLUGIN_NPM_CACHE

SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]

USER root

RUN [[ "${OMNIGENT_BUILD_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || { echo "OMNIGENT_BUILD_DIGEST must be an exact sha256 build identity" >&2; exit 1; }

# Install every approved vendor CLI at image build time.
RUN npm config set fund false \
    && npm config set audit false \
    && npm config set update-notifier false \
    && npm install -g --include=optional --no-audit --no-fund \
        "@openai/codex@${CODEX_CLI_VERSION}" \
        "@anthropic-ai/claude-code@${CLAUDE_CLI_VERSION}" \
        "opencode-ai@${OPENCODE_VERSION}" \
    && echo "Verifying vendor runtime installations..." \
    && command -v codex >/dev/null || { echo "codex binary missing after install" >&2; exit 1; } \
    && command -v claude >/dev/null || { echo "claude binary missing after install" >&2; exit 1; } \
    && command -v opencode >/dev/null || { echo "opencode binary missing after install" >&2; exit 1; }

# Verify each installed vendor version is inside its trusted runtime-pack
# supported range (inclusive lower, exclusive upper). A drifted runtime fails
# the build instead of becoming launch authority.
RUN CODEX_INSTALLED="$(codex --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)" \
    && CLAUDE_INSTALLED="$(claude --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)" \
    && OPENCODE_INSTALLED="$(opencode --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)" \
    && [ -n "${CODEX_INSTALLED}" ] || { echo "could not parse codex --version" >&2; exit 1; } \
    && [ -n "${CLAUDE_INSTALLED}" ] || { echo "could not parse claude --version" >&2; exit 1; } \
    && [ -n "${OPENCODE_INSTALLED}" ] || { echo "could not parse opencode --version" >&2; exit 1; } \
    && python3 -c "import sys; from packaging.version import Version; iv=Version('${CODEX_INSTALLED}'); minv=Version('${CODEX_MIN_VERSION}'); maxv=Version('${CODEX_MAX_VERSION}'); sys.exit(0 if minv <= iv < maxv else 1)" \
        || { echo "codex version ${CODEX_INSTALLED} outside supported range ${CODEX_MIN_VERSION}..${CODEX_MAX_VERSION} (exclusive upper)" >&2; exit 1; } \
    && python3 -c "import sys; from packaging.version import Version; iv=Version('${CLAUDE_INSTALLED}'); minv=Version('${CLAUDE_MIN_VERSION}'); maxv=Version('${CLAUDE_MAX_VERSION}'); sys.exit(0 if minv <= iv < maxv else 1)" \
        || { echo "claude version ${CLAUDE_INSTALLED} outside supported range ${CLAUDE_MIN_VERSION}..${CLAUDE_MAX_VERSION} (exclusive upper)" >&2; exit 1; } \
    && python3 -c "import sys; from packaging.version import Version; iv=Version('${OPENCODE_INSTALLED}'); minv=Version('${OPENCODE_MIN_VERSION}'); maxv=Version('${OPENCODE_MAX_VERSION}'); sys.exit(0 if minv <= iv < maxv else 1)" \
        || { echo "opencode version ${OPENCODE_INSTALLED} outside supported range ${OPENCODE_MIN_VERSION}..${OPENCODE_MAX_VERSION} (exclusive upper)" >&2; exit 1; } \
    && echo "codex ${CODEX_INSTALLED}, claude ${CLAUDE_INSTALLED}, opencode ${OPENCODE_INSTALLED} within supported ranges"

# Warm the OpenCode plugin SDK dependency closure at image build time.
#
# The first `opencode serve` in a config directory writes
# `package.json` = {"dependencies": {"@opencode-ai/plugin": "<opencode version>"}}
# next to `opencode.json` and runs `npm install` there. Populate an image-owned
# npm cache with exactly that closure and prove a second install resolves from
# it with the network disabled, so the workflow launch only copies files and
# never installs from registry.npmjs.org.
RUN set -eu \
    && warm="$(mktemp -d)" \
    && verify="$(mktemp -d)" \
    && printf '{"dependencies":{"@opencode-ai/plugin":"%s"}}\n' "${OPENCODE_VERSION}" > "${warm}/package.json" \
    && cp "${warm}/package.json" "${verify}/package.json" \
    && (cd "${warm}" && npm install --cache "${OPENCODE_PLUGIN_NPM_CACHE}" --no-audit --no-fund --prefer-online) \
    && (cd "${verify}" && npm install --cache "${OPENCODE_PLUGIN_NPM_CACHE}" --no-audit --no-fund --offline) \
    && test -f "${verify}/node_modules/@opencode-ai/plugin/package.json" \
    && rm -rf "${warm}" "${verify}" "${OPENCODE_PLUGIN_NPM_CACHE}/_logs" \
    && chown -R 1000:1000 "${OPENCODE_PLUGIN_NPM_CACHE}" \
    && chmod -R u+rwX,go+rX "${OPENCODE_PLUGIN_NPM_CACHE}" \
    && echo "opencode plugin npm cache warmed at ${OPENCODE_PLUGIN_NPM_CACHE} for @opencode-ai/plugin@${OPENCODE_VERSION}"

# The exact-host launcher owns the portable numeric runtime contract. Upstream
# Omnigent images may run as root or may name UID 1000 differently, so ensure
# that identity and its writable home exist without depending on a
# provider-owned username.
RUN if ! getent group 1000 >/dev/null; then groupadd --gid 1000 app; fi \
    && if ! getent passwd 1000 >/dev/null; then useradd --uid 1000 --gid 1000 --home-dir /home/app --shell /bin/sh app; fi \
    && mkdir -p /home/app \
    && chown 1000:1000 /home/app

USER 1000:1000
WORKDIR /home/app

ENV HOME="/home/app" \
    PATH="/usr/local/bin:${PATH}"

# Sanity check as non-root
RUN command -v codex && codex --version \
    && command -v claude && claude --version \
    && command -v opencode && opencode --version

LABEL org.opencontainers.image.title="omnigent-host-moonmind" \
      org.opencontainers.image.description="Neutral shared MoonMind Omnigent host image (Codex ${CODEX_CLI_VERSION}, Claude Code ${CLAUDE_CLI_VERSION}, OpenCode ${OPENCODE_VERSION})" \
      org.opencontainers.image.source="https://github.com/MoonLadderStudios/MoonMind" \
      moonmind.omnigent.build_digest="${OMNIGENT_BUILD_DIGEST}" \
      moonmind.codex.version="${CODEX_CLI_VERSION}" \
      moonmind.claude.version="${CLAUDE_CLI_VERSION}" \
      moonmind.opencode.version="${OPENCODE_VERSION}" \
      moonmind.opencode.plugin_npm_cache="${OPENCODE_PLUGIN_NPM_CACHE}"
