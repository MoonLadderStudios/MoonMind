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

Run `moonspec-assess`. Read `artifacts/moonspec/source-acceptance.json` only when its `featureId` matches the active feature, then write `artifacts/moonspec/acceptance-assessment.json` with the same `featureId` before planning. If no matching source acceptance matrix exists, report that assessment is not applicable and do not block planning.
