# Data Model: Policy-Gated Image Upload and Submit

## AttachmentPolicy

Represents server-provided runtime attachment rules.

Fields:
- `enabled`: whether attachment entry points are available.
- `allowedContentTypes`: allowed MIME types for selected files.
- `maxCount`: maximum total attachments across objective and step targets.
- `maxBytes`: maximum bytes for one selected file.
- `totalBytes`: maximum bytes across selected and persisted attachments.

Validation:
- Disabled policy hides entry points and blocks submitted attachment refs.
- Empty or missing allowed content types fall back to the product image defaults.
- Count, per-file size, total size, and content type are validated before upload and before submit.

## DraftAttachment

Represents one selected or persisted attachment in the Create page draft.

Fields:
- `filename`: user-visible attachment name.
- `contentType`: MIME type from the browser, Jira import, or persisted artifact ref.
- `sizeBytes`: byte size used for validation and display.
- `artifactId`: present only after upload or when reconstructed from persisted state.
- `target`: objective or specific step owner.
- `state`: selected, uploading, uploaded, invalid, failed, preview_failed, or persisted.
- `errorMessage`: target-scoped validation, upload, or preview failure text when applicable.

Validation:
- Draft attachments are owned by exactly one target.
- Invalid, failed, incomplete, or uploading attachments block create, edit, and rerun submit.
- Failed or invalid attachments can be removed without clearing unrelated draft state.
- Upload retry affects only the failed target.

## ObjectiveAttachmentRef

Represents an artifact-backed attachment submitted with the task objective.

Fields:
- `artifactId`
- `filename`
- `contentType`
- `sizeBytes`

Validation:
- Objective refs are submitted only through `task.inputAttachments`.
- Objective refs are never copied into step attachment fields automatically.

## StepAttachmentRef

Represents an artifact-backed attachment submitted with one owning step.

Fields:
- `artifactId`
- `filename`
- `contentType`
- `sizeBytes`

Validation:
- Step refs are submitted only through the owning `task.steps[n].inputAttachments`.
- Reordering or editing steps must not move a step attachment to another target.

## State Transitions

```text
selected -> invalid
selected -> uploading
uploading -> uploaded
uploading -> failed
uploaded -> preview_failed
failed -> uploading
invalid -> removed
failed -> removed
preview_failed -> removed
persisted -> removed
```

Rules:
- `invalid`, `failed`, and `uploading` are submit-blocking.
- `preview_failed` preserves metadata and remove actions; it is not allowed to corrupt the draft.
- `uploaded` and `persisted` become structured refs in the task payload.
- `removed` attachments are excluded from submit payloads.
