# Data Model: Enforce Image Artifact Storage and Policy

## Input Attachment Ref

Represents one structured attachment target in a task-shaped execution payload.

Fields:
- `artifactId`: Artifact identifier for previously uploaded image bytes.
- `filename`: Operator-visible original filename.
- `contentType`: Declared image content type.
- `sizeBytes`: Declared byte size.

Validation:
- No unknown fields are accepted.
- `artifactId`, `filename`, and `contentType` must be non-empty strings.
- `sizeBytes` must be a non-negative integer.
- `contentType` must be server-allowed and must not be `image/svg+xml`.

## Attachment Policy

Server-defined policy exposed to the Create page and enforced by API boundaries.

Fields:
- `enabled`
- `maxCount`
- `maxBytes`
- `totalBytes`
- `allowedContentTypes`

Validation:
- Disabled policy rejects any submitted image refs.
- Allowed types default to `image/png`, `image/jpeg`, and `image/webp`.
- `image/svg+xml` is rejected even if a caller attempts to configure it.

## Attachment Artifact

Existing Temporal artifact row containing uploaded image bytes and metadata.

Relevant fields:
- `artifact_id`
- `content_type`
- `size_bytes`
- `sha256`
- `status`
- `metadata_json`
- `storage_key`

Validation:
- Status must be `COMPLETE` before execution start.
- Metadata is used for diagnostics, not target binding.
- Actual completed bytes must match declared size/hash when declarations exist.
- Magic bytes must match the accepted content type for image attachment artifacts.

## Task Snapshot

Existing original task input snapshot artifact.

Relevant fields:
- `draft.task.inputAttachments`
- `draft.task.steps[n].inputAttachments`
- `attachmentRefs`

Validation:
- Target binding comes from the task fields, not artifact metadata.
- `attachmentRefs` records normalized refs for visibility and reconstruction.

## Execution Artifact Visibility

Existing execution record artifact refs and artifact links.

Rules:
- Submitted attachment artifact IDs are attached to execution artifact refs after workflow creation.
- Link metadata remains secondary observability and must not retarget attachments.
