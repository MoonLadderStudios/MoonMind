---
name: moonspec-breakdown-output-gitignored
description: moonspec-breakdown writes to artifacts/story-breakdowns/ which is gitignored, so the post-run git commit is a no-op by design
metadata:
  type: project
---

The `moonspec-breakdown` skill writes `stories.json` + `stories.md` under `artifacts/story-breakdowns/<run-slug>/`. That path matches `artifacts*/` in `.gitignore` (line ~49), so it is intentionally NOT versioned — breakdown output is a temporary derived view; the canonical `docs/` source stays the source of truth.

**Why:** When a task wraps the skill with "commit your work (`git add -A && git commit`)", the commit finds nothing to stage and reports `nothing to commit, working tree clean`. This is the correct outcome, not a failure.

**How to apply:** Run the commit as instructed but expect a no-op; report it faithfully. Do NOT `git add -f` the breakdown output to force a commit — that contradicts the documented gitignore design (SKILL.md "repository gitignore; not versioned"; CLAUDE.md lists `artifacts/` as a gitignored handoff path).
