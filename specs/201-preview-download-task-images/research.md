# Research: Preview and Download Task Images by Target

## Runtime Intent

Decision: Treat MM-373 as runtime behavior, not documentation-only work.
Rationale: The Jira brief asks for user-visible task detail, edit, and rerun behavior backed by preview/download controls.
Alternatives considered: Docs-only alignment was rejected because no docs-only request was made.

## Target Binding Source

Decision: Use authoritative artifact/task snapshot metadata for target grouping and do not infer target from filenames.
Rationale: `docs/Tasks/ImageSystem.md` requires objective/step grouping and forbids filename-based target inference.
Alternatives considered: Filename prefixes or artifact ordering were rejected because they violate the source design.

## Detail Page Rendering

Decision: Add target-aware image rendering to task detail using the existing execution artifact list response.
Rationale: The artifact list already returns metadata and artifact IDs; using it avoids extra requests and keeps access through MoonMind-owned endpoints.
Alternatives considered: Fetch each artifact's full metadata separately was rejected as unnecessary for the target-aware UI contract.

## Edit And Rerun Durability

Decision: Preserve the existing edit/rerun reconstruction path and rely on focused regression tests already covering unchanged persisted refs, explicit removal, and persisted-vs-local validation.
Rationale: The existing implementation reconstructs from authoritative snapshots and serializes persisted refs unless explicitly removed.
Alternatives considered: Rewriting edit/rerun attachment state was rejected as higher risk and outside MM-373's remaining detail-page gap.

## Preview Failure Handling

Decision: Keep filename/metadata and download actions visible when an image `onError` fires.
Rationale: Preview failure must not remove metadata visibility or download actions.
Alternatives considered: Hiding failed previews without fallback was rejected because it obscures attachment scope.
