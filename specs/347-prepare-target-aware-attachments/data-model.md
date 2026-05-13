# Data Model: Prepare-Time Target-Aware Attachment Materialization

## Attachment Target

- `targetKind`: `objective` or `step`.
- `targetRef`: implicit task objective for objective targets; stable `stepRef` for step targets.
- `stepOrdinal`: optional diagnostic order only; never the authoritative binding.

Validation:
- Step targets require a non-empty stable `stepRef`.
- Objective targets must not carry `stepRef`.

## Prepared Attachment

- `artifactId`: source artifact identifier.
- `filename`: sanitized original filename metadata.
- `contentType`: MIME type metadata when known.
- `sizeBytes`: source size metadata when known.
- `targetKind`: objective or step.
- `stepRef`: required for step targets.
- `rawInputRef`: lightweight source artifact ref.
- `derivedContextRef`: lightweight target-aware context ref.
- `workspacePath`: stable workspace-relative materialized path when materialized by the worker.
- `status`: preparation status such as `prepared` for successful manifest entries.

Validation:
- Prepared attachment metadata must not contain inline binary data, data URLs, or generated markdown payloads.
- Workspace paths are metadata refs, not binary payloads.

## Attachments Manifest

- `manifestRef`: compact workflow-visible manifest ref.
- `entries`: every prepared attachment entry for objective and step targets.

Validation:
- Every authored attachment must appear exactly once per authored target binding.
- Duplicate artifact IDs across different targets remain separate entries.
- Manifest data remains bounded metadata suitable for workflow/activity boundaries.

## Target-Aware Image Context

- `targetKind`: objective or step.
- `stepRef`: required for step targets.
- `attachmentRefs`: artifact IDs included for that target.
- `contextPath` or `derivedContextRef`: lightweight ref to generated context artifact.

Validation:
- Context artifacts are grouped by target and must not collapse unrelated step attachments into a shared bucket.
