#!/usr/bin/env bash
set -euo pipefail

case "${CODEX_CLI_VERSION}" in
    (""|*[!0-9A-Za-z._-]*) echo "Invalid CODEX_CLI_VERSION: ${CODEX_CLI_VERSION}" >&2; exit 1;;
esac
case "${CLAUDE_CLI_VERSION}" in
    (""|*[!0-9A-Za-z._-]*) echo "Invalid CLAUDE_CLI_VERSION: ${CLAUDE_CLI_VERSION}" >&2; exit 1;;
esac

stub_codex() {
    mkdir -p /usr/local/lib/node_modules/@openai/codex
    printf 'codex stub installed during fallback.\n' > /usr/local/lib/node_modules/@openai/codex/LICENSE
    printf '%s\n' \
        "#!/usr/bin/env bash" \
        "echo \"codex stub: package unavailable during image build\" >&2" \
        "exit 0" > /usr/local/bin/codex
    chmod +x /usr/local/bin/codex
}

stub_claude() {
    mkdir -p /usr/local/lib/node_modules/@anthropic-ai/claude-code
    printf 'claude-code stub installed during fallback; package unavailable.\n' > /usr/local/lib/node_modules/@anthropic-ai/claude-code/LICENSE
    printf '%s\n' \
        "#!/usr/bin/env bash" \
        "echo \"claude-code stub: package unavailable during image build\" >&2" \
        "exit 0" > /usr/local/bin/claude
    chmod +x /usr/local/bin/claude
}

if [ "${INSTALL_CODEX_CLI}" != "true" ]; then
    echo "INSTALL_CODEX_CLI=${INSTALL_CODEX_CLI}; skipping Codex install and using stubs" >&2
    stub_codex
else
    if ! npm install -g @openai/codex@"${CODEX_CLI_VERSION}"; then
        echo "Warning: Failed to install @openai/codex; installing stub" >&2
        stub_codex
    else
        CODEX_LINK_TARGET=$(readlink -f /usr/local/bin/codex)
        echo "Resolving codex symlink target: ${CODEX_LINK_TARGET}"
        rm /usr/local/bin/codex
        printf '%s\n' \
            "#!/bin/sh" \
            "exec node \"${CODEX_LINK_TARGET}\" \"\$@\"" > /usr/local/bin/codex
        chmod +x /usr/local/bin/codex
    fi
fi

if [ "${INSTALL_CLAUDE_CLI}" != "true" ]; then
    echo "INSTALL_CLAUDE_CLI=${INSTALL_CLAUDE_CLI}; skipping Claude CLI install and using stub" >&2
    stub_claude
elif ! npm install -g @anthropic-ai/claude-code@"${CLAUDE_CLI_VERSION}"; then
    install_status=$?
    echo "Warning: Failed to install Claude CLI (exit ${install_status}); installing stub binary" >&2
    stub_claude
fi

if [ ! -f /usr/local/lib/node_modules/@openai/codex/LICENSE ]; then
    mkdir -p /usr/local/lib/node_modules/@openai/codex
    touch /usr/local/lib/node_modules/@openai/codex/LICENSE
fi
if [ ! -f /usr/local/lib/node_modules/@anthropic-ai/claude-code/LICENSE ]; then
    mkdir -p /usr/local/lib/node_modules/@anthropic-ai/claude-code
    touch /usr/local/lib/node_modules/@anthropic-ai/claude-code/LICENSE
fi

if [ "${CLEAN_NPM_CACHE:-false}" = "true" ]; then
    npm cache clean --force || true
fi
