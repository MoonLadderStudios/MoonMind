---
name: moonspec-assess
description: Assess source acceptance coverage before MoonSpec planning.
metadata:
  required-capabilities:
    - git
---

# MoonSpec Assess

Use this skill after `moonspec-specify` and before `moonspec-plan` when a source-backed spec may include `artifacts/moonspec/source-acceptance.json`.

Before assessing, resolve the active feature directory and feature identifier from `.specify/feature.json` or the selected `specs/<feature>/spec.md`. Consume `artifacts/moonspec/source-acceptance.json` only when its `featureId` matches the active feature. If the artifact is absent or belongs to a different feature, report `not_applicable` and do not write or require an assessment artifact for this story.

## Output

Write `artifacts/moonspec/acceptance-assessment.json` with schema version `v1` and the same `featureId` as the active feature and source acceptance matrix.

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
