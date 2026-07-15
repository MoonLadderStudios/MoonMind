#!/usr/bin/env bash
# Shared CI helper: compute the exact changed-file list for the current event.
#
# MoonLadderStudios/MoonMind#3326: one reusable classifier so the selector,
# frontend selection, deployment-safety validation, and the generated-contract
# detector do not maintain subtly different event logic. It relies only on a
# shallow checkout plus explicit fetches of the exact base/head commits.
#
# Usage:
#   bash tools/ci/compute_changed_files.sh [OUTPUT_FILE]
#
# Behavior:
#   - Writes the newline-delimited changed-file list to OUTPUT_FILE
#     (default: /tmp/changed-files.txt).
#   - Prints exactly one line to stdout: "resolution=known" or
#     "resolution=unknown" so callers can pick their own fail-open direction.
#   - A "known" resolution means the exact base/head tree diff was computed
#     (the file may legitimately be empty when nothing changed).
#   - An "unknown" resolution means the change set could not be determined
#     (manual/scheduled runs, missing/zero base SHAs, or unavailable commits).
#     The output file is emptied so callers stay conservative/fail-open.
#
# Event coverage: pull_request, push (with a real non-zero before SHA),
# merge_group, and everything else (workflow_dispatch, schedule, first push)
# is treated as an unknown change set.
set -euo pipefail

OUTPUT_FILE="${1:-/tmp/changed-files.txt}"

ensure_commit_available() {
  local sha="$1"

  if git cat-file -e "${sha}^{commit}" 2>/dev/null; then
    return 0
  fi

  git fetch --no-tags --depth=1 origin "$sha" >/dev/null 2>&1
}

# Classify the event into a base/head pair. Empty values mean "unknown".
read -r base_sha head_sha < <(
  python3 - <<'PY'
import json
import os

event_name = os.environ.get("GITHUB_EVENT_NAME", "")
event_path = os.environ.get("GITHUB_EVENT_PATH", "")
head_env = os.environ.get("GITHUB_SHA", "")
zero = "0" * 40

data = {}
if event_path:
    try:
        with open(event_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        data = {}


def dig(*keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current or ""


base = ""
head = ""
if event_name == "pull_request":
    base = dig("pull_request", "base", "sha")
    head = dig("pull_request", "head", "sha")
elif event_name == "merge_group":
    base = dig("merge_group", "base_sha")
    head = dig("merge_group", "head_sha")
elif event_name == "push":
    before = data.get("before") or ""
    if before and before != zero:
        base = before
        head = data.get("after") or head_env
# workflow_dispatch / schedule / first push / unknown -> leave empty.

if not base or not head or base == zero or head == zero:
    base = ""
    head = ""

print(f"{base}\t{head}")
PY
)

if [[ -z "${base_sha}" || -z "${head_sha}" ]]; then
  : > "${OUTPUT_FILE}"
  echo "resolution=unknown"
  exit 0
fi

if ! ensure_commit_available "${base_sha}" || ! ensure_commit_available "${head_sha}"; then
  : > "${OUTPUT_FILE}"
  echo "resolution=unknown"
  exit 0
fi

if ! git diff --name-only "${base_sha}" "${head_sha}" > "${OUTPUT_FILE}" 2>/dev/null; then
  : > "${OUTPUT_FILE}"
  echo "resolution=unknown"
  exit 0
fi

echo "resolution=known"
