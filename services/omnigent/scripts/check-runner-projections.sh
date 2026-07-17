#!/bin/sh
set -eu

skills=${MOONMIND_ACTIVE_SKILLS_DIR:?MOONMIND_ACTIVE_SKILLS_DIR is required}
test -d "$skills"
test -r "$skills/_manifest.json"
test ! -w "$skills"
test -r /opt/moonmind-tools/manifest.json
test -x /opt/moonmind-tools/bin/gh
command -v gh >/dev/null
gh --version >/dev/null
