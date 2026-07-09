---
description: Assess source acceptance coverage and produce the acceptance assessment artifact.
handoffs:
  - label: Create Plan
    agent: moonspec.plan
    prompt: Use the assessment to plan bounded work
    send: true
---

## User Input

```text
$ARGUMENTS
```

Run `moonspec-assess`. Read `artifacts/moonspec/source-acceptance.json` when it exists and write `artifacts/moonspec/acceptance-assessment.json` before planning.
