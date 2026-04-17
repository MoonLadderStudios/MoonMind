# Research: Targeted Image Attachment Submission

## Canonical Attachment Ref Shape

Decision: Model `TaskInputAttachmentRef` as a compact object with `artifactId`, `filename`, `contentType`, and `sizeBytes`.

Rationale: `docs/Tasks/ImageSystem.md` defines this as the canonical reference shape, and the existing Create page already emits that object when uploading step attachments.

Alternatives considered: Reusing legacy `attachments` or `attachmentIds` fields was rejected because the source design explicitly excludes queue-specific attachment routes and fields from the desired-state contract.

## Validation Boundary

Decision: Validate attachment refs in `moonmind/workflows/tasks/task_contract.py` and normalize them again in the `/api/executions` task-shaped router before workflow start.

Rationale: The task contract covers canonical task payload construction, while the router builds `MoonMind.Run` initial parameters directly and currently copies only selected task fields. Both boundaries need evidence so malformed refs fail fast and valid refs are preserved into workflow input.

Alternatives considered: Frontend-only validation was rejected because API clients can submit task-shaped requests directly.

## Raw Bytes And Data URLs

Decision: Reject fields or values that embed raw image bytes, base64 content, or `data:image/...` URLs inside attachment refs.

Rationale: The source design forbids raw bytes and data URLs in execution create payloads, task instruction markdown, and workflow histories. Explicit validation gives deterministic failure instead of silently storing large or unsafe payloads.

Alternatives considered: Silently stripping raw fields was rejected because it can hide author intent and make edit/rerun reconstruction misleading.

## Snapshot Reconstruction

Decision: Preserve attachment bindings in the existing original task input snapshot artifact by keeping refs under `task.inputAttachments` and `task.steps[n].inputAttachments`.

Rationale: Snapshot artifacts already preserve the task payload for edit/rerun reconstruction. Keeping the canonical fields there avoids new tables and aligns with the source design's authoritative snapshot contract.

Alternatives considered: Adding a new persisted attachment-binding table was rejected because the story requires no new storage and the snapshot already carries the task-shaped input.

## Testing Strategy

Decision: Add unit tests for contract validation and router normalization, plus contract coverage that verifies `/api/executions` persists snapshot attachment refs.

Rationale: Unit tests cover fast failure modes; contract tests cover the API and artifact snapshot boundary that downstream edit/rerun depends on.

Alternatives considered: Full Docker-backed integration was rejected for this story because the acceptance boundary is task-shaped submission and workflow-start payload construction, not artifact materialization or vision context generation.
