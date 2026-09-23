#!/usr/bin/env bash
# Decide whether a published MoonMind host needs a rebuild from its upstream
# host base. The Omnigent server is a separate artifact and may be released
# before or after the host image.
set -euo pipefail

published_ref="${1:?published host image required}"
base_ref="${2:?upstream host base image required}"

docker pull "$base_ref" >/dev/null
if ! docker pull "$published_ref" >/dev/null; then
  echo 'rebuild_required=true' >> "$GITHUB_OUTPUT"
  exit 0
fi

if [[ "$base_ref" =~ @(sha256:[0-9a-f]{64})$ ]]; then
  base_digest="${BASH_REMATCH[1]}"
else
  base_repo_digest="$(docker inspect --format='{{index .RepoDigests 0}}' "$base_ref")"
  base_digest="${base_repo_digest##*@}"
fi
if [[ ! "$base_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "::error::Could not resolve upstream Omnigent host base digest" >&2
  exit 1
fi

published_base_digest="$(docker inspect --format='{{index .Config.Labels "moonmind.omnigent.build_digest"}}' "$published_ref" 2>/dev/null || true)"
if [[ "$published_base_digest" == "$base_digest" ]]; then
  echo 'rebuild_required=false' >> "$GITHUB_OUTPUT"
else
  echo 'rebuild_required=true' >> "$GITHUB_OUTPUT"
fi
