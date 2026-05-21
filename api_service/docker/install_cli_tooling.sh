#!/usr/bin/env bash
set -euo pipefail

case "${CODEX_CLI_VERSION}" in
    (""|*[!0-9A-Za-z._-]*) echo "Invalid CODEX_CLI_VERSION: ${CODEX_CLI_VERSION}" >&2; exit 1;;
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
    printf '%s\n' \
        "#!/usr/bin/env bash" \
        "echo \"codex stub: package unavailable during image build\" >&2" \
        "exit 0" > /usr/local/bin/codex
    chmod +x /usr/local/bin/codex
}

stub_gemini() {
    mkdir -p /usr/local/lib/node_modules/@google/gemini-cli
    printf 'gemini-cli stub installed during fallback; no upstream package available.\n' > /usr/local/lib/node_modules/@google/gemini-cli/LICENSE
    printf '%s\n' \
        "#!/usr/bin/env bash" \
        "echo \"gemini-cli stub: package unavailable during image build\" >&2" \
        "exit 0" > /usr/local/bin/gemini
    chmod +x /usr/local/bin/gemini
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
    npm cache clean --force || true
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

if [ "${INSTALL_GEMINI_CLI}" != "true" ]; then
    echo "INSTALL_GEMINI_CLI=${INSTALL_GEMINI_CLI}; skipping Gemini CLI install and using stub" >&2
    stub_gemini
elif ! npm install -g @google/gemini-cli@"${GEMINI_CLI_VERSION}"; then
    install_status=$?
    echo "Warning: Failed to install Gemini CLI (exit ${install_status}); installing stub binary" >&2
    stub_gemini
else
    GEMINI_LINK_TARGET=$(readlink -f /usr/local/bin/gemini)
    echo "Resolving gemini symlink target: ${GEMINI_LINK_TARGET}"
    rm /usr/local/bin/gemini
    printf '%s\n' \
        "#!/bin/sh" \
        "exec node \"${GEMINI_LINK_TARGET}\" \"\$@\"" > /usr/local/bin/gemini
    chmod +x /usr/local/bin/gemini
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
if [ ! -f /usr/local/lib/node_modules/@google/gemini-cli/LICENSE ]; then
    mkdir -p /usr/local/lib/node_modules/@google/gemini-cli
    touch /usr/local/lib/node_modules/@google/gemini-cli/LICENSE
fi
if [ ! -f /usr/local/lib/node_modules/@anthropic-ai/claude-code/LICENSE ]; then
    mkdir -p /usr/local/lib/node_modules/@anthropic-ai/claude-code
    touch /usr/local/lib/node_modules/@anthropic-ai/claude-code/LICENSE
fi

npm cache clean --force || true
