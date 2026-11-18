#!/bin/sh
# Verify the actual image runtime with a fresh SDK-backed plugin, no network
# and no credentials. Health alone does not prove plugin readiness.
set -eu

image=${1:?usage: verify-warm-plugin-cache.sh <image-ref> [ready-timeout-seconds] [expected-major|auto]}
ready_timeout=${2:-90}
expected_major=${3:-2}

exec docker run --rm --network none --user 1000:1000 \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m,mode=1777 \
  --tmpfs /home/app:rw,nosuid,nodev,size=512m,uid=1000,gid=1000 \
  --env HOME=/home/app \
  --env OPENCODE_DISABLE_MODELS_FETCH=1 \
  --env OPENCODE_DISABLE_DEFAULT_PLUGINS=1 \
  --env OPENCODE_DISABLE_AUTOUPDATE=1 \
  --env MOONMIND_READY_TIMEOUT="${ready_timeout}" \
  --env MOONMIND_EXPECTED_OPENCODE_MAJOR="${expected_major}" \
  --entrypoint /bin/sh "${image}" -eu -c '
seed=/opt/moonmind/opencode-npm-cache
cache=$HOME/.omnigent/moonmind/opencode-npm-cache
test -d "$seed" || { echo "image is missing the warm plugin npm cache at $seed" >&2; exit 78; }
mkdir -p "$HOME/.omnigent/moonmind"
cp -a "$seed" "$cache"
printf "%s\n" "cache=$cache" "prefer-offline=true" "audit=false" "fund=false" "update-notifier=false" > "$HOME/.npmrc"
chmod 0600 "$HOME/.npmrc"

config_home=$(mktemp -d)
config=$config_home/opencode
workspace=$(mktemp -d)
plugin=$config/moonmind-probe
mkdir -p "$plugin"
version=$(opencode --version | sed -n "s/^\(opencode v\)\{0,1\}\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)$/\2/p")
actual_major=${version%%.*}
expected_major=${MOONMIND_EXPECTED_OPENCODE_MAJOR:-2}
if [ "$expected_major" != auto ] && [ "$actual_major" != "$expected_major" ]; then
  echo "unexpected OpenCode major for release: expected $expected_major, observed $actual_major" >&2
  exit 1
fi
case "$version" in
  1.*)
    # Retained v1 images remain valid bootstrap/recovery candidates. Select
    # their actual protocol, never fall back here after a v2 failure.
    major=1
    printf "{\"private\":true,\"type\":\"module\",\"dependencies\":{\"@opencode-ai/plugin\":\"%s\"}}\n" "$version" > "$config/package.json"
    (cd "$config" && npm install --offline --no-audit --no-fund)
    test -f "$config/node_modules/@opencode-ai/plugin/package.json"
    cat > "$config/moonmind-probe.ts" <<EOF
import type { Plugin } from "@opencode-ai/plugin";
import { writeFileSync } from "node:fs";
export const MoonMindProbe: Plugin = async () => {
  writeFileSync("$workspace/plugin-loaded", "ready");
  return {};
};
EOF
    printf "{\"plugin\":[\"%s/moonmind-probe.ts\"],\"permission\":\"ask\"}\n" "$config" > "$config/opencode.json"
    ;;
  2.*)
    major=2
    # The installed binary owns this dependency version, including overrides.
    printf "{\"name\":\"moonmind-probe\",\"private\":true,\"type\":\"module\",\"dependencies\":{\"@opencode/plugin\":\"%s\"}}\n" "$version" > "$plugin/package.json"
    (cd "$plugin" && npm install --offline --no-audit --no-fund)
    test -f "$plugin/node_modules/@opencode/plugin/package.json"
    cat > "$plugin/server.js" <<EOF
import { Plugin } from "@opencode/plugin";
import { writeFileSync } from "node:fs";
export default Plugin.define({
  id: "moonmind-offline-probe",
  setup() { writeFileSync("$workspace/plugin-loaded", "ready"); }
});
EOF
    cat > "$config/opencode.json" <<EOF
{"plugins":["$plugin"],"permissions":[{"action":"*","resource":"*","effect":"ask"}]}
EOF
    ;;
  *) echo "unsupported OpenCode version output" >&2; exit 1 ;;
esac
XDG_CONFIG_HOME=$config_home
XDG_DATA_HOME=$(mktemp -d)
XDG_CACHE_HOME=$(mktemp -d)
XDG_STATE_HOME=$(mktemp -d)
OPENCODE_PASSWORD=moonmind-offline-probe
OPENCODE_SERVER_PASSWORD=$OPENCODE_PASSWORD
export XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_STATE_HOME OPENCODE_PASSWORD OPENCODE_SERVER_PASSWORD
cd "$workspace"

opencode serve --port 4096 --hostname 127.0.0.1 >"$workspace/serve.log" 2>&1 &
pid=$!
trap "kill $pid 2>/dev/null || true; wait $pid 2>/dev/null || true" EXIT HUP INT TERM
deadline=$(( $(date +%s) + MOONMIND_READY_TIMEOUT ))
ready=
while [ "$(date +%s)" -lt "$deadline" ]; do
  if ! kill -0 "$pid" 2>/dev/null; then break; fi
  if [ "$major" = 1 ]; then
    code=$(curl -s -u "opencode:$OPENCODE_PASSWORD" -o "$workspace/info.json" -w "%{http_code}" --max-time 3 http://127.0.0.1:4096/global/health || true)
    if [ "$code" = 200 ] && jq -e --arg version "$version" ".healthy == true and .version == \$version" "$workspace/info.json" >/dev/null; then
      code=$(curl -s -u "opencode:$OPENCODE_PASSWORD" -o "$workspace/sessions.json" -w "%{http_code}" --max-time 3 http://127.0.0.1:4096/session || true)
      if [ "$code" = 200 ] && [ -f "$workspace/plugin-loaded" ] && jq -e "arrays" "$workspace/sessions.json" >/dev/null; then ready=1; break; fi
    fi
    sleep 0.5
    continue
  fi
  code=$(curl -s -u "opencode:$OPENCODE_PASSWORD" -o "$workspace/info.json" -w "%{http_code}" --max-time 3 http://127.0.0.1:4096/api/info || true)
  if [ "$code" = 200 ] && jq -e --arg version "$version" ".version == \$version" "$workspace/info.json" >/dev/null; then
    # Plugin activation is asynchronous and location-scoped. Poll until our
    # SDK import and setup execute, not merely until a listener responds.
    code=$(curl -s -u "opencode:$OPENCODE_PASSWORD" -o "$workspace/plugins.json" -w "%{http_code}" --max-time 3 http://127.0.0.1:4096/api/plugin || true)
    if [ "$code" = 200 ] && [ -f "$workspace/plugin-loaded" ] && jq -e ".data | any(.id == \"moonmind-offline-probe\" and .state.status == \"active\")" "$workspace/plugins.json" >/dev/null; then ready=1; break; fi
  fi
  sleep 0.5
done
if [ -z "$ready" ]; then
  echo "opencode v${major} plugin did not become active offline within ${MOONMIND_READY_TIMEOUT}s" >&2
  tail -n 40 "$workspace/serve.log" >&2 || true
  cat "$workspace/plugins.json" >&2 2>/dev/null || true
  exit 1
fi
test ! -e "$HOME/.npm" || {
  echo "npm ignored the .npmrc cache path and used $HOME/.npm" >&2
  exit 1
}
echo "opencode v${major} SDK plugin became active offline from the warm npm cache"
'
