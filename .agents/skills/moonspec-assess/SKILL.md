---
name: moonspec-assess
description: Assess source acceptance coverage before MoonSpec planning.
metadata:
  required-capabilities:
    - git
---

# MoonSpec Assess

Use this skill after `moonspec-specify` and before `moonspec-plan` when a source-backed spec may include `artifacts/moonspec/source-acceptance.json`.

## Output

Write `artifacts/moonspec/acceptance-assessment.json` with schema version `v1`.

External systems can provide a matrix, but MoonSpec stays provider neutral.

Allowed overall verdicts:

- `FULLY_IMPLEMENTED`
- `PARTIALLY_IMPLEMENTED`
- `NOT_IMPLEMENTED`
- `BLOCKED`
- `NO_DETERMINATION`

Allowed row statuses:

- `VERIFIED`
- `PARTIAL`
- `MISSING`
- `CONFLICT`
- `UNVERIFIED`
- `OUT_OF_SCOPE`

## Bounded Backlog

Produce `boundedBacklog` for every missing, partial, conflict, or required-unverified row. The backlog is the authoritative implementation backlog for downstream planning, tasks, implementation, and verification.

Do not choose `FULLY_IMPLEMENTED` unless every repo-verifiable source row is verified and every manual/provider-only row is explicitly scoped.
