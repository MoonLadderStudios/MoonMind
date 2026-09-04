#!/bin/sh
# Prove that an omnigent-host-opencode image starts `opencode serve` for a
# plugin-enabled session with the network disabled, using only the image-owned
# npm cache the Dockerfile warms for `@opencode-ai/plugin`.
#
# This is the executable contract behind MoonMind's on-demand OpenCode host.
# OpenCode runs `npm install @opencode-ai/plugin@<version>` inside every fresh
# per-session config directory on its first `serve`; MoonMind's host entrypoint
# copies /opt/moonmind/opencode-npm-cache into the run-owned state volume and
# writes $HOME/.npmrc so that install resolves from the cache instead of
# registry.npmjs.org. The check below mirrors that seam inside a throwaway
# container with a fresh tmpfs home, like a workflow launch, and fails when the
# image still needs the registry to become ready.
#
# Usage: verify-warm-plugin-cache.sh <image-ref> [ready-timeout-seconds]
set -eu

image=${1:?usage: verify-warm-plugin-cache.sh <image-ref> [ready-timeout-seconds]}
ready_timeout=${2:-90}

docker run --rm --network none --user 1000:1000 \
  --tmpfs /home/app:rw,nosuid,nodev,size=512m,uid=1000,gid=1000 \
  --env HOME=/home/app \
  --env OPENCODE_DISABLE_MODELS_FETCH=1 \
  --env OPENCODE_DISABLE_DEFAULT_PLUGINS=1 \
  --env OPENCODE_DISABLE_AUTOUPDATE=1 \
  --env MOONMIND_READY_TIMEOUT="${ready_timeout}" \
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
mkdir -p "$config"
cat > "$config/moonmind-probe.ts" <<EOF
import type { Plugin } from "@opencode-ai/plugin"
export const MoonMindProbe: Plugin = async () => ({})
EOF
cat > "$config/opencode.json" <<EOF
{"plugin":["$config/moonmind-probe.ts"],"permission":"ask"}
EOF
XDG_CONFIG_HOME=$config_home
XDG_DATA_HOME=$(mktemp -d)
export XDG_CONFIG_HOME XDG_DATA_HOME
cd "$workspace"

opencode serve --port 4096 --hostname 127.0.0.1 >"$workspace/serve.log" 2>&1 &
pid=$!
deadline=$(( $(date +%s) + MOONMIND_READY_TIMEOUT ))
ready=
while [ "$(date +%s)" -lt "$deadline" ]; do
  if ! kill -0 "$pid" 2>/dev/null; then break; fi
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:4096/session || true)
  if [ -n "$code" ] && [ "$code" != 000 ] && [ "$code" -lt 500 ]; then ready=1; break; fi
  sleep 0.5
done
kill "$pid" 2>/dev/null || true
if [ -z "$ready" ]; then
  echo "opencode serve did not become ready offline within ${MOONMIND_READY_TIMEOUT}s" >&2
  tail -n 40 "$workspace/serve.log" >&2 || true
  exit 1
fi
test -f "$config/node_modules/@opencode-ai/plugin/package.json" || {
  echo "opencode did not install the plugin SDK from the warm cache" >&2
  exit 1
}
test ! -e "$HOME/.npm" || {
  echo "npm ignored the .npmrc cache path and used $HOME/.npm" >&2
  exit 1
}
echo "opencode serve became ready offline from the warm plugin npm cache"
'
