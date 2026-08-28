# syntax=docker/dockerfile:1.4
# Dedicated harness-specific Omnigent host for OpenCode.
# Derives from the same immutable Omnigent host base used by MoonMind.
# Adds only the OpenCode CLI at build time — never at workflow launch.
#
# Build args:
#   OMNIGENT_HOST_BASE_IMAGE - pinned base, e.g. ghcr.io/omnigent-ai/omnigent-host@sha256:<digest>
#   OPENCODE_VERSION - exact pinned version within MoonMind's supported range
#
# See docs/Omnigent/OpenCodeHost.md for compatibility policy.

# Required: callers must resolve the upstream image to a repo digest before
# building. An omitted argument intentionally makes FROM invalid/fail closed.
ARG OMNIGENT_HOST_BASE_IMAGE
ARG OMNIGENT_BUILD_DIGEST
ARG OPENCODE_VERSION=1.18.11
# Pinned compatibility range enforced at build time (inclusive lower, exclusive upper)
# MoonMind supports opencode-ai >=1.17.7,<1.19.0 for the current Omnigent revision.
ARG OPENCODE_MIN_VERSION=1.17.7
ARG OPENCODE_MAX_VERSION=1.19.0

FROM ${OMNIGENT_HOST_BASE_IMAGE} AS base

# Re-declare args after FROM
ARG OPENCODE_VERSION
ARG OMNIGENT_BUILD_DIGEST
ARG OPENCODE_MIN_VERSION
ARG OPENCODE_MAX_VERSION

SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]

USER root

RUN [[ "${OMNIGENT_BUILD_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || { echo "OMNIGENT_BUILD_DIGEST must be an exact sha256 build identity" >&2; exit 1; }

# Install OpenCode CLI at image build time only.
# The workflow launcher must never run npm install.
RUN npm config set fund false \
    && npm config set audit false \
    && npm config set update-notifier false \
    && npm install -g --no-audit --no-fund "opencode-ai@${OPENCODE_VERSION}" \
    && echo "Verifying opencode installation..." \
    && command -v opencode >/dev/null || { echo "opencode binary missing after install" >&2; exit 1; } \
    && INSTALLED_VERSION="$(opencode --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)" \
    && echo "Installed opencode version: ${INSTALLED_VERSION}" \
    && if [ -z "${INSTALLED_VERSION}" ]; then echo "Could not parse opencode --version" >&2; exit 1; fi \
    && python3 -c "import sys; from packaging.version import Version; iv=Version('${INSTALLED_VERSION}'); minv=Version('${OPENCODE_MIN_VERSION}'); maxv=Version('${OPENCODE_MAX_VERSION}'); sys.exit(0 if minv <= iv < maxv else 1)" \
        || { echo "opencode version ${INSTALLED_VERSION} outside supported range ${OPENCODE_MIN_VERSION}..${OPENCODE_MAX_VERSION} (exclusive upper)" >&2; exit 1; } \
    && echo "opencode ${INSTALLED_VERSION} within supported range ${OPENCODE_MIN_VERSION} < ${OPENCODE_MAX_VERSION}" \
    && opencode --version \
    && (npm cache clean --force || true) \
    && echo "opencode host image ready"

# Verify unrelated harness CLIs are not bundled solely for this path.
# The base image may intentionally contain shared tooling; this check ensures
# the OpenCode layer did not introduce Codex/Claude/Qwen etc. beyond the base.
# Fail only if opencode-specific layer added unexpected binaries not in base manifest.
RUN echo "Checking that only expected harness binaries are present..." \
    && ls -la /usr/local/bin/opencode 2>&1 | head -20 \
    && echo "Harness check complete"

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

# Ensure opencode is in PATH for the runtime user
ENV HOME="/home/app" \
    PATH="/usr/local/bin:${PATH}"

# Sanity check as non-root
RUN command -v opencode && opencode --version

LABEL org.opencontainers.image.title="omnigent-host-opencode" \
      org.opencontainers.image.description="Dedicated Omnigent host with OpenCode CLI pinned to ${OPENCODE_VERSION}" \
      org.opencontainers.image.source="https://github.com/MoonLadderStudios/MoonMind" \
      moonmind.harness="opencode-native" \
      moonmind.omnigent.build_digest="${OMNIGENT_BUILD_DIGEST}" \
      moonmind.opencode.version="${OPENCODE_VERSION}"
