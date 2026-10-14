---
description: Analyze and automatically remediate MoonSpec artifact drift across spec.md, plan.md, tasks.md, and related design files without asking for user input.
scripts:
  sh: .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
---

## User Input

```text
$ARGUMENTS
```

Run `moonspec-align` for the active feature, passing the user input above unchanged. The skill owns prerequisites, hooks, analysis, bounded artifact edits, and reporting.
