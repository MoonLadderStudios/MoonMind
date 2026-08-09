#!/bin/sh
set -eu

source_gh=${MOONMIND_GH_SOURCE:-/usr/bin/gh}
source_moonmind=${MOONMIND_CONTAINER_CLI_SOURCE:-/opt/moonmind/moonmind-container-cli.py}
output=${MOONMIND_TOOL_BUNDLE_OUTPUT:-/output}
version=${MOONMIND_GH_VERSION:?MOONMIND_GH_VERSION is required}

test -x "$source_gh"
if [ ! -r "$source_moonmind" ]; then
  source_moonmind=$(dirname "$0")/moonmind-container-cli.py
fi
test -r "$source_moonmind"
chmod u+w "$output" "$output/bin" 2>/dev/null || true
chmod -R u+w "$output" 2>/dev/null || true
rm -rf "$output/bin" "$output/manifest.json"
mkdir -p "$output/bin"
install -m 0555 "$source_gh" "$output/bin/gh"
install -m 0555 "$source_moonmind" "$output/bin/moonmind"
reported=$($output/bin/gh --version | sed -n '1p')
case "$reported" in
  *" $version "*) ;;
  *) echo "gh version mismatch: expected $version" >&2; exit 65 ;;
esac
printf '{"schemaVersion":1,"bundleVersion":"gh-%s-container-v1","tools":[{"name":"gh","version":"%s","path":"bin/gh","versionProbe":["--version"]},{"name":"moonmind","version":"container-v1","path":"bin/moonmind","versionProbe":["--help"]}]}\n' \
  "$version" "$version" > "$output/manifest.json"
chmod -R a-w "$output"
