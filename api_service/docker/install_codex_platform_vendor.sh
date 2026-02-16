#!/usr/bin/env bash
set -euo pipefail

platform_tag="linux-x64"
case "$(uname -m)" in
    x86_64 | amd64)
        platform_tag="linux-x64"
        ;;
    aarch64 | arm64)
        platform_tag="linux-arm64"
        ;;
esac

resolved_codex_version="${CODEX_CLI_VERSION}"
if [ -f /usr/local/lib/node_modules/@openai/codex/package.json ]; then
    resolved_codex_version="$(node -p "require('/usr/local/lib/node_modules/@openai/codex/package.json').version" 2>/dev/null || true)"
fi
if [ -z "${resolved_codex_version}" ]; then
    echo "Warning: could not determine installed codex version; skipping platform vendor install" >&2
    exit 0
fi

case "${resolved_codex_version}" in
    *-linux-x64 | *-linux-arm64 | *-darwin-x64 | *-darwin-arm64 | *-win32-x64 | *-win32-arm64)
        vendor_version="${resolved_codex_version}"
        ;;
    *)
        vendor_version="${resolved_codex_version}-${platform_tag}"
        ;;
esac

platform_tmp_dir="$(mktemp -d)"
platform_archive="$(cd "$platform_tmp_dir" && npm pack "@openai/codex@${vendor_version}")" || true
if [ -z "$platform_archive" ] || [ ! -f "$platform_tmp_dir/$platform_archive" ]; then
    echo "Warning: did not receive platform codex archive ${vendor_version}; continuing with base package only" >&2
    rm -rf "$platform_tmp_dir"
    exit 0
fi

tar -xzf "$platform_tmp_dir/$platform_archive" -C "$platform_tmp_dir"
if [ -d "$platform_tmp_dir/package/vendor" ]; then
    mkdir -p /usr/local/lib/node_modules/@openai/codex/vendor
    cp -r "$platform_tmp_dir/package/vendor/." /usr/local/lib/node_modules/@openai/codex/vendor/
else
    echo "Warning: no vendor directory in ${vendor_version}; codex may fail to execute" >&2
fi
rm -rf "$platform_tmp_dir"
