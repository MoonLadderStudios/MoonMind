# Data Model: Preview and Download Task Images by Target

## Task Image Input

Represents a persisted image artifact shown on task detail, edit, or rerun surfaces.

Fields:
- `artifactId`: stable artifact identifier used for MoonMind-owned download endpoints.
- `contentType`: image MIME type.
- `sizeBytes`: artifact byte count for reviewer metadata.
- `filename`: display name from artifact metadata.
- `target`: authoritative objective or step binding.

Validation:
- `contentType` must start with `image/` before detail preview rendering.
- `target` must come from authoritative metadata; missing target metadata keeps the artifact in the generic artifact list only.

## Attachment Target

Represents where a task image belongs.

Variants:
- `objective`: image belongs to task-level instructions/objective.
- `step`: image belongs to one step and carries a step label or identity.

Validation:
- UI must not derive target from filename, artifact ID, ordering, or preview content.

## Persisted Attachment Ref

Represents an existing artifact ref reconstructed into edit/rerun drafts.

Fields:
- `artifactId`
- `filename`
- `contentType`
- `sizeBytes`

State transitions:
- `available`: unchanged ref remains in the draft.
- `removed`: reviewer explicitly removes the ref.
- `replaced`: reviewer explicitly removes or changes refs by adding new local files.

## Local Attachment File

Represents a new browser-selected file not yet uploaded.

Validation:
- Must be shown separately from persisted refs.
- Must pass the existing attachment policy before upload.
