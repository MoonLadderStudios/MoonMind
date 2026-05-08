# Data Model: Route Binary Inputs Through Authorized Artifact Refs

## Binary Input Artifact

Represents bytes uploaded through MoonMind artifact APIs before task execution submission.

Fields:
- `artifactId`: Stable MoonMind artifact identifier, required in structured attachment refs.
- `contentType`: Normalized MIME type validated against server-defined attachment policy.
- `sizeBytes`: Declared and verified byte size.
- `sha256`: Optional declared digest, verified when provided during upload completion.
- `status`: `pending_upload`, `complete`, `failed`, or `deleted`.
- `createdByPrincipal`: Principal that created the artifact.
- `metadata`: Observability fields such as filename, source, target kind, or step reference; not authoritative for target meaning.

Validation rules:
- Only `complete` artifacts may be submitted for execution.
- Declared content type and size must match stored artifact metadata when present.
- Unsupported content types, including SVG images for input attachments, are rejected.
- Unauthorized principals cannot attach another principal's artifact to execution.

State transitions:
- `pending_upload` -> `complete` after successful write or multipart completion.
- `pending_upload` -> `failed` after integrity, type, size, or storage completion failure.
- `complete` -> `deleted` through artifact lifecycle deletion.

## Upload Intent

Represents the server-created upload session returned by `POST /api/artifacts`.

Fields:
- `mode`: `single_put` or `multipart`.
- `uploadUrl`: Direct upload endpoint or storage presign URL.
- `uploadId`: Multipart upload identifier when multipart is selected.
- `expiresAt`: Time after which upload completion is no longer valid.
- `maxSizeBytes`: Maximum direct upload size.
- `requiredHeaders`: Headers the browser must send for storage-backed uploads.

Validation rules:
- Browser submission must wait for upload completion/finalization.
- Expired or incomplete upload intents cannot produce accepted execution refs.

## Structured Attachment Ref

Execution-facing lightweight reference for a finalized binary artifact.

Fields:
- `artifactId`: Required artifact identifier.
- `filename`: Required display/original filename.
- `contentType`: Required normalized content type.
- `sizeBytes`: Required non-negative integer.
- `targetKind`: Derived from the task contract as `objective` or `step` during validation/linking.
- `stepRef` or `stepOrdinal`: Present for step-scoped attachments when available.

Validation rules:
- Refs must contain only the supported fields in task payloads.
- Duplicate artifact refs within one task submission are rejected.
- The same binary ref cannot be silently retargeted by metadata or storage path.

## Artifact Access Request

Represents preview, download, presign-download, or worker materialization of an artifact.

Fields:
- `artifactId`: Requested artifact.
- `principal`: User or service principal making the request.
- `executionRef`: Namespace, workflow ID, run ID, and link type when access is execution-scoped.
- `readMode`: Metadata, preview, raw download, or worker materialization.

Validation rules:
- Browser access goes through MoonMind APIs.
- Raw download requires owner, view permission, or service authorization.
- Worker materialization uses service authorization and fails explicitly when not authorized.

## Execution Artifact Link

Associates an artifact with an execution after task submission accepts the structured ref.

Fields:
- `artifactId`
- `namespace`
- `workflowId`
- `runId`
- `linkType`: `input.attachment` for submitted binary inputs.
- `label`: Usually the submitted filename.

Validation rules:
- Links are execution-scoped and must not be treated as globally reusable permission grants.
- Linking must not bypass artifact ownership or service authorization checks.
