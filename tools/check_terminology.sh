#!/usr/bin/env bash
# WP4 docs/instruction-scope banned-term terminology check (Task -> Workflow hard switch).
#
# Fails (exit 1) with file:line output when banned legacy "task" product
# terminology appears in the documentation or agent-instruction scope:
#   - docs/**/*.md, excluding:
#       docs/tmp/**                                  (migration/rollout notes)
#       docs/ReleaseNotes/**                         (historical)
#       docs/Temporal/WorkflowLanguageHardSwitchPlan.md (defines the banned terms)
#   - README.md
#   - AGENTS.md (CLAUDE.md / GEMINI.md are symlinks to it)
#   - .agents/skills/**/*.md
#
# Banned patterns (case-insensitive unless noted):
#   docs/Tasks/ and ../Tasks/ path references (case-sensitive; stale doc links)
#   task-oriented
#   task-first
#   MoonMind task
#   task-scoped
#   task console
#   task dashboard
#   specs/<feature>/ and MoonSpec feature artifact guidance in canonical docs
#
# False-positive avoidance and known limitations:
#   - Inline backtick code spans and fenced code blocks are stripped before
#     matching, so quoted live identifiers (e.g. `task_dashboard` paths kept
#     until the code rename) do not trip the check.
#   - Reserved qualified terms (Temporal Task, Workflow Task, Activity Task,
#     Task Queue, Jira task, Codex provider task) do not overlap the banned
#     patterns above, so no reserved-term allowlisting is needed yet.
#   - This is a docs/instruction-scope gate only. WP9 extends enforcement to code scope
#     (taskId/task_id product fields, /tasks routes, OpenAPI surfaces) per
#     docs/Temporal/WorkflowLanguageHardSwitchPlan.md section 18.
#   - Multi-line constructs (a banned phrase split across a hard line wrap)
#     are not detected; matching is per physical line after code stripping.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

python3 - << 'PYEOF'
import pathlib
import re
import sys

ROOT = pathlib.Path(".")

EXCLUDED_PREFIXES = (
    "docs/tmp/",
    "docs/ReleaseNotes/",
)
EXCLUDED_FILES = {
    "docs/Temporal/WorkflowLanguageHardSwitchPlan.md",
}

BANNED = [
    # (label, compiled pattern)
    ("docs/Tasks/ path", re.compile(r"(?:docs|\.\.)/Tasks/")),
    ("task-oriented", re.compile(r"task-oriented", re.IGNORECASE)),
    ("task-first", re.compile(r"task-first", re.IGNORECASE)),
    ("MoonMind task", re.compile(r"MoonMind task", re.IGNORECASE)),
    ("task-scoped", re.compile(r"task-scoped", re.IGNORECASE)),
    ("task console", re.compile(r"task console", re.IGNORECASE)),
    ("task dashboard", re.compile(r"task dashboard", re.IGNORECASE)),
    ("specs/<feature>/ guidance", re.compile(r"specs/<feature>")),
    ("MoonSpec feature artifact guidance", re.compile(r"MoonSpec feature artifacts?", re.IGNORECASE)),
]

INLINE_CODE = re.compile(r"`[^`]*`")


def in_scope(rel: str) -> bool:
    if rel in ("README.md", "AGENTS.md"):
        return True
    if rel.startswith(".agents/skills/") and rel.endswith(".md"):
        return True
    if not rel.startswith("docs/") or not rel.endswith(".md"):
        return False
    if rel in EXCLUDED_FILES:
        return False
    return not any(rel.startswith(p) for p in EXCLUDED_PREFIXES)


def iter_files():
    yield from (p for p in (ROOT / "docs").rglob("*.md"))
    agents_skills = ROOT / ".agents" / "skills"
    if agents_skills.exists():
        yield from (p for p in agents_skills.rglob("*.md"))
    for name in ("README.md", "AGENTS.md"):
        p = ROOT / name
        if p.exists():
            yield p


findings = []
seen = set()
for path in iter_files():
    rel = path.as_posix()
    if rel in seen or not in_scope(rel):
        continue
    seen.add(rel)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        scannable = INLINE_CODE.sub("", line)
        for label, pattern in BANNED:
            m = pattern.search(scannable)
            if m:
                findings.append(f"{rel}:{lineno}: banned term '{label}': {m.group(0)!r}")

if findings:
    print("Docs terminology check FAILED. Banned legacy task terminology found:")
    for f in findings:
        print(f"  {f}")
    print(
        "\nSee docs/Temporal/WorkflowLanguageHardSwitchPlan.md (sections 1, 5, 18) "
        "for the reserved-term and replacement rules."
    )
    sys.exit(1)

print(f"Docs terminology check passed ({len(seen)} files scanned).")
PYEOF
