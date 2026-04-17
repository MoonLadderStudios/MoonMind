# Data Model: Expose Image Diagnostics and Failure Evidence

## ImageDiagnosticEvent

Compact event describing one image-input lifecycle observation.

Fields:
- `event`: Stable event class, such as `attachment_upload_started`, `attachment_upload_completed`, `attachment_validation_failed`, `prepare_download_started`, `prepare_download_completed`, `prepare_download_failed`, `image_context_generation_started`, `image_context_generation_completed`, `image_context_generation_failed`, or `image_context_generation_disabled`.
- `status`: `started`, `completed`, `failed`, or `disabled`.
- `targetKind`: `objective` or `step`.
- `stepRef`: Present when `targetKind` is `step`.
- `stepOrdinal`: Present when known for step-scoped attachments.
- `artifactId`: Present for attachment-scoped events.
- `filename`: Present for attachment-scoped events when known.
- `contentType`: Present for attachment-scoped events when known.
- `sizeBytes`: Present for attachment-scoped events when known.
- `workspacePath`: Present after prepare download has a deterministic local path.
- `manifestPath`: Present when the attachment manifest path is available.
- `contextPath`: Present when a target context path is available.
- `error`: Sanitized failure detail for failed events.

Validation rules:
- `targetKind` must be `objective` or `step`.
- `stepRef` is required for step-scoped target events when the step target is known.
- Raw image bytes, credentials, auth headers, and storage-provider secrets are forbidden.
- Target binding must come from authoritative task input or manifest metadata.

## ImageDiagnosticsSummary

Prepared task diagnostics summary for image inputs.

Fields:
- `events`: Ordered list of `ImageDiagnosticEvent`.
- `manifestPath`: Absolute or workspace-local manifest path when produced.
- `contextIndexPath`: Workspace-local image context index path when produced.
- `contextTargets`: Per-target context status, context path, target kind, step ref, attachment refs, and source paths.
- `attachmentCount`: Number of declared image input attachments.

Validation rules:
- Evidence paths must be strings only and must not embed file contents.
- `contextTargets` must preserve explicit target identity.
- Empty image input sets produce an empty event list and zero attachment count.

## DiagnosticEvidencePath

MoonMind-owned path to an artifact that helps debug image inputs.

Fields:
- `kind`: `manifest`, `context_index`, or `context`.
- `path`: Workspace-local or task artifact path.
- `targetKind`: Present for target-specific context paths.
- `stepRef`: Present for step-specific context paths.

Validation rules:
- Paths must point to MoonMind-controlled workspace or artifact locations.
- Paths must not be provider URLs or raw object-storage URLs.
