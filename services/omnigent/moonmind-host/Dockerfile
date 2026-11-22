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
#   OMNIGENT_BUILD_DIGEST    - exact sha256 digest of the upstream host base
#                              used to build this image
#
# Vendor CLIs are installed at build time only; the workflow launcher never
# installs runtimes. "latest" installs the newest release this base's Omnigent
# supports that npm has fully published (tools/vendor_cli_releases.py), so new
# models reach hosts without a repository edit. The release workflow passes the
# exact versions it resolved so labels and layer caches name them; an exact
# version holds that CLI. Exact-host attestation records the observed release.

ARG OMNIGENT_HOST_BASE_IMAGE
ARG OMNIGENT_BUILD_DIGEST
ARG CODEX_CLI_VERSION=latest
ARG CLAUDE_CLI_VERSION=latest
ARG OPENCODE_VERSION=latest
ARG OPENCODE_PLUGIN_NPM_CACHE=/opt/moonmind/opencode-npm-cache
# Image-owned helper tools (MoonLadderStudios/MoonMind#4558): gh and the
# non-secret MoonMind container CLI. The single pin source is
# services/omnigent/tools/manifest.lock.json; these args mirror it and the
# build fails if they drift from the manifest.
ARG OMNIGENT_GH_VERSION=2.76.2

FROM ${OMNIGENT_HOST_BASE_IMAGE} AS base

# Re-declare args after FROM
ARG OMNIGENT_BUILD_DIGEST
ARG CODEX_CLI_VERSION
ARG CLAUDE_CLI_VERSION
ARG OPENCODE_VERSION
ARG OPENCODE_PLUGIN_NPM_CACHE
ARG OMNIGENT_GH_VERSION

SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends jq \
    && rm -rf /var/lib/apt/lists/*

RUN [[ "${OMNIGENT_BUILD_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || { echo "OMNIGENT_BUILD_DIGEST must be an exact sha256 build identity" >&2; exit 1; }

# Install every approved vendor CLI at image build time, choosing releases
# against the supported ranges of this exact Omnigent base. The chosen releases
# are recorded for the steps below.
COPY tools/vendor_cli_releases.py /tmp/vendor_cli_releases.py
RUN npm config set fund false \
    && npm config set audit false \
    && npm config set update-notifier false \
    && npm uninstall -g --no-audit --no-fund opencode-ai \
    && mkdir -p /opt/moonmind \
    && specs="$(/opt/venv/bin/python /tmp/vendor_cli_releases.py --omnigent-bounds \
        --json /opt/moonmind/vendor-cli-versions.json --npm-specs \
        "codex=${CODEX_CLI_VERSION}" \
        "claude=${CLAUDE_CLI_VERSION}" \
        "opencode=${OPENCODE_VERSION}")" \
    && rm -f /tmp/vendor_cli_releases.py \
    && echo "Installing ${specs}" \
    && npm install -g --include=optional --no-audit --no-fund ${specs} \
    && echo "Verifying vendor runtime installations..." \
    && command -v codex >/dev/null || { echo "codex binary missing after install" >&2; exit 1; } \
    && command -v claude >/dev/null || { echo "claude binary missing after install" >&2; exit 1; } \
    && command -v opencode >/dev/null || { echo "opencode binary missing after install" >&2; exit 1; }

# Image-owned Omnigent helper tools (MoonLadderStudios/MoonMind#4558).
#
# gh and the non-secret MoonMind container CLI are installed into
# /opt/moonmind-tools/bin at build time from the pins in
# services/omnigent/tools/manifest.lock.json (single source). The selected
# image owns its tool contents for the host's lifetime: new tool contents
# require a new image build, and host launch never downloads, installs, or
# copies shared tool binaries. The installed files are root-owned and
# unwritable by the runtime user; the logical `docker` capability stays
# capability-mediated by the unchanged moonmind-container-cli.py behavior.
COPY services/omnigent/moonmind-host/install_moonmind_tools.py /tmp/install_moonmind_tools.py
COPY services/omnigent/tools/manifest.lock.json /tmp/manifest.lock.json
COPY services/omnigent/scripts/moonmind-container-cli.py /tmp/moonmind-container-cli.py
COPY services/omnigent/scripts/moonmind-tools.sh /tmp/moonmind-tools.sh
RUN GH_VERSION="${OMNIGENT_GH_VERSION}" \
    MANIFEST_PATH=/tmp/manifest.lock.json \
    CLI_SOURCE=/tmp/moonmind-container-cli.py \
    PROFILE_SOURCE=/tmp/moonmind-tools.sh \
    OUTPUT_ROOT=/opt/moonmind-tools \
    PROFILE_TARGET=/etc/profile.d/moonmind-tools.sh \
    python3 /tmp/install_moonmind_tools.py \
    && rm -f /tmp/install_moonmind_tools.py /tmp/manifest.lock.json \
        /tmp/moonmind-container-cli.py /tmp/moonmind-tools.sh \
    && test -x /opt/moonmind-tools/bin/gh \
    && test -x /opt/moonmind-tools/bin/moonmind \
    && test -r /opt/moonmind-tools/manifest.json \
    && test -r /etc/profile.d/moonmind-tools.sh

# Warm the OpenCode plugin SDK dependency closure at image build time.
#
# OpenCode 2 uses directory plugins with the @opencode/plugin SDK. Keep its
# dependency closure available for plugin-enabled sessions without registry
# access. The release smoke installs a fresh SDK-backed plugin offline and
# verifies that the authenticated v2 server actually activates it.
RUN set -eu \
    && opencode_version="$(jq -er .opencode /opt/moonmind/vendor-cli-versions.json)" \
    && warm="$(mktemp -d)" \
    && verify="$(mktemp -d)" \
    && printf '{"dependencies":{"@opencode/plugin":"%s"}}\n' "${opencode_version}" > "${warm}/package.json" \
    && cp "${warm}/package.json" "${verify}/package.json" \
    && (cd "${warm}" && npm install --cache "${OPENCODE_PLUGIN_NPM_CACHE}" --no-audit --no-fund --prefer-online) \
    && (cd "${verify}" && npm install --cache "${OPENCODE_PLUGIN_NPM_CACHE}" --no-audit --no-fund --offline) \
    && test -f "${verify}/node_modules/@opencode/plugin/package.json" \
    && rm -rf "${warm}" "${verify}" "${OPENCODE_PLUGIN_NPM_CACHE}/_logs" \
    && chown -R 1000:1000 "${OPENCODE_PLUGIN_NPM_CACHE}" \
    && chmod -R u+rwX,go+rX "${OPENCODE_PLUGIN_NPM_CACHE}" \
    && echo "opencode plugin npm cache warmed at ${OPENCODE_PLUGIN_NPM_CACHE} for @opencode/plugin@${opencode_version}"

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
    PATH="/opt/moonmind-tools/bin:/opt/venv/bin:/usr/local/bin:${PATH}"

# Sanity check as non-root. The printed vendor releases are build provenance,
# not admission; the native CLI readiness check below decides usability.
RUN command -v codex && codex --version \
    && command -v claude && claude --version \
    && command -v opencode && opencode --version \
    && command -v gh && gh --version \
    && command -v moonmind && moonmind --help \
    && jq --version && jq -en '1 + 1 == 2' \
    && bash -lc 'command -v gh && command -v moonmind && jq -en "1 + 1 == 2"'

# Portable fan-out Skills use ordinary python3 and require the queue client's
# HTTP dependency. Use the existing image environment in both shell forms;
# a system Python without its packages must never reach workflow launch.
RUN --network=none python3 -c 'import fcntl, httpx' \
    && bash -lc "python3 -c 'import fcntl, httpx'"

# A binary and its release number alone do not prove the installed
# Omnigent native harness can use it. Check that owner's actual readiness
# contract without credentials or network before publishing the image.
RUN --network=none /opt/venv/bin/python - <<'PY'
from omnigent.onboarding.harness_readiness import harness_cli_installed

families = ("openai", "anthropic", "opencode")
unavailable = [family for family in families if not harness_cli_installed(family, timeout=10)]
if unavailable:
    raise SystemExit(f"Omnigent native CLI readiness failed: {unavailable}")
print("Omnigent native CLI readiness verified for Codex, Claude, and OpenCode")
PY

LABEL org.opencontainers.image.title="omnigent-host-moonmind" \
      org.opencontainers.image.description="Neutral shared MoonMind Omnigent host image (Codex ${CODEX_CLI_VERSION}, Claude Code ${CLAUDE_CLI_VERSION}, OpenCode ${OPENCODE_VERSION})" \
      org.opencontainers.image.source="https://github.com/MoonLadderStudios/MoonMind" \
      moonmind.omnigent.build_digest="${OMNIGENT_BUILD_DIGEST}" \
      moonmind.codex.version="${CODEX_CLI_VERSION}" \
      moonmind.claude.version="${CLAUDE_CLI_VERSION}" \
      moonmind.opencode.version="${OPENCODE_VERSION}" \
      moonmind.gh.version="${OMNIGENT_GH_VERSION}" \
      moonmind.tools.bundle="gh-${OMNIGENT_GH_VERSION}-container-v1" \
      moonmind.opencode.plugin_npm_cache="${OPENCODE_PLUGIN_NPM_CACHE}"
