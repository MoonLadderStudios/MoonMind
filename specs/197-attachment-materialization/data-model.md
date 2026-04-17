# Data Model: Materialize Attachment Manifest and Workspace Files

## Input Attachment Ref

Represents a declared input attachment already validated before prepare.

Fields:
- `artifactId`: required MoonMind artifact id.
- `filename`: required original filename for operator context and deterministic sanitized output naming.
- `contentType`: required normalized MIME type.
- `sizeBytes`: required byte size.

Validation:
- Must be an object from `task.inputAttachments` or `task.steps[n].inputAttachments`.
- Must not include embedded binary or data URL content.
- Target meaning comes from the containing field, not from the ref itself.

## Attachment Target

Represents where a declared attachment belongs.

Fields:
- `targetKind`: `objective` or `step`.
- `stepRef`: required for step targets in materialized output.
- `stepOrdinal`: optional zero-based source step index for diagnostics.

State rules:
- Objective attachments come only from `task.inputAttachments`.
- Step attachments come only from `task.steps[n].inputAttachments`.
- Explicit step ids are preferred as `stepRef`.
- Missing step ids receive deterministic ordinal fallback references.

## Attachment Manifest Entry

Represents one materialized attachment in `.moonmind/attachments_manifest.json`.

Fields:
- `artifactId`
- `filename`
- `contentType`
- `sizeBytes`
- `targetKind`
- `workspacePath`
- `stepRef` when `targetKind` is `step`
- `stepOrdinal` when `targetKind` is `step`
- `visionContextPath` when later context generation produces one
- `sourceArtifactPath` when a source artifact path is exposed

Validation:
- One entry exists for every successfully materialized declared attachment.
- `workspacePath` points inside `.moonmind/inputs/objective/` or `.moonmind/inputs/steps/<stepRef>/`.
- Entries are deterministic for the same canonical payload.

## Materialized Attachment File

Represents the local workspace copy consumed by runtimes.

Path rules:
- Objective: `.moonmind/inputs/objective/<artifactId>-<sanitized-filename>`
- Step: `.moonmind/inputs/steps/<stepRef>/<artifactId>-<sanitized-filename>`

Validation:
- Filename is sanitized to prevent path traversal.
- Artifact id prefix prevents collisions for repeated filenames.
- Missing or failed writes fail prepare.

## Prepare Failure

Represents a terminal prepare-stage failure for partial materialization.

Fields:
- `artifactId` when known.
- `targetKind` when known.
- `stepRef` and `stepOrdinal` for step targets when known.
- Human-readable reason.

State transition:
- `declared` -> `materialized` when bytes are downloaded, written, and represented in manifest.
- `declared` -> `failed` when any download/write/manifest step fails.
- `failed` stops prepare and prevents runtime execution.
