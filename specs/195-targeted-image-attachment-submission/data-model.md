# Data Model: Targeted Image Attachment Submission

## TaskInputAttachmentRef

Represents one lightweight image attachment reference submitted in a task-shaped execution request.

Fields:
- `artifactId`: Non-empty MoonMind artifact identifier.
- `filename`: Non-empty original filename for operator visibility.
- `contentType`: Non-empty MIME type, expected to be an allowed image type by policy.
- `sizeBytes`: Non-negative byte size for the uploaded artifact.

Validation:
- Must be an object.
- Must not include raw bytes, base64 payloads, inline content, or data URL fields.
- String values must not begin with `data:image/`.
- Missing required compact metadata fails validation.

## Attachment Target Binding

Represents the target meaning derived from where a ref appears in the task-shaped payload.

Target rules:
- `task.inputAttachments` means objective-scoped attachment.
- `task.steps[n].inputAttachments` means step-scoped attachment for that step.
- Target meaning is never inferred from filename, artifact id, or link metadata.

## Task Input Snapshot

Existing artifact-backed snapshot of the original task-shaped input.

Relevant fields:
- `draft.task.inputAttachments`
- `draft.task.steps[n].inputAttachments`

Validation:
- Snapshot data must preserve canonical attachment fields for edit/rerun reconstruction.
- Snapshot reconstruction must not rely on legacy `attachments`, `attachmentIds`, or `attachment_ids` mutation fields.
