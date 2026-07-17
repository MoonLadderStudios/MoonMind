#!/bin/sh
set -eu

source_gh=${MOONMIND_GH_SOURCE:-/usr/bin/gh}
output=${MOONMIND_TOOL_BUNDLE_OUTPUT:-/output}
version=${MOONMIND_GH_VERSION:?MOONMIND_GH_VERSION is required}

test -x "$source_gh"
if [ -x "$output/bin/gh" ] && [ -r "$output/manifest.json" ]; then
  reported=$($output/bin/gh --version | sed -n '1p')
  case "$reported" in
    *" $version "*) exit 0 ;;
    *) echo "existing tool bundle does not match requested gh $version" >&2; exit 65 ;;
  esac
fi
mkdir -p "$output/bin"
install -m 0555 "$source_gh" "$output/bin/gh"
reported=$($output/bin/gh --version | sed -n '1p')
case "$reported" in
  *" $version "*) ;;
  *) echo "gh version mismatch: expected $version" >&2; exit 65 ;;
esac
printf '{"schemaVersion":1,"bundleVersion":"gh-%s","tools":[{"name":"gh","version":"%s","path":"bin/gh","versionProbe":["--version"]}]}\n' \
  "$version" "$version" > "$output/manifest.json"
chmod -R a-w "$output"
