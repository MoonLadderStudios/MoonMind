#!/bin/sh
set -eu

output=${OMNIGENT_TOOL_BUNDLE_OUTPUT:-/output}
bundle_version=${OMNIGENT_TOOL_BUNDLE_VERSION:?tool bundle version is required}
expected_sha=${OMNIGENT_GH_SHA256:?expected gh SHA-256 is required}
source_gh=$(command -v gh)
actual_sha=$(sha256sum "$source_gh" | awk '{print $1}')

[ "$actual_sha" = "$expected_sha" ] || {
  echo "gh checksum does not match the deployment-pinned checksum" >&2
  exit 65
}

if [ -f "$output/manifest.json" ]; then
  grep -Fq "\"bundleVersion\": \"$bundle_version\"" "$output/manifest.json" || exit 66
  grep -Fq "\"sha256\": \"$expected_sha\"" "$output/manifest.json" || exit 66
  "$output/bin/gh" --version >/dev/null
  exit 0
fi

stage="$output/.stage-$$"
rm -rf "$stage"
mkdir -p "$stage/bin"
cp "$source_gh" "$stage/bin/gh"
chmod 0555 "$stage/bin/gh"
"$stage/bin/gh" --version >/dev/null
gh_version=$("$stage/bin/gh" --version | sed -n '1s/^gh version \([^ ]*\).*/\1/p')
printf '{\n  "schemaVersion": 1,\n  "bundleVersion": "%s",\n  "tools": [\n    {"name": "gh", "version": "%s", "platform": "linux/%s", "sha256": "%s", "path": "bin/gh", "versionProbe": ["--version"]}\n  ]\n}\n' \
  "$bundle_version" "$gh_version" "$(uname -m)" "$expected_sha" > "$stage/manifest.json"
mv "$stage/bin" "$output/bin"
mv "$stage/manifest.json" "$output/manifest.json"
rmdir "$stage"
