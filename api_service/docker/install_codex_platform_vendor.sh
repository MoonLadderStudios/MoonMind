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

vendor_version="${CODEX_CLI_VERSION}-${platform_tag}"
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
