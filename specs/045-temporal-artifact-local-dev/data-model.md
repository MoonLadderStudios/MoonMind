# Data Model: Temporal Local Artifact System

## Entity: Artifact

- **Description**: Immutable artifact metadata row indexed in Postgres with storage location in MinIO/S3-compatible backend.
- **Fields**:
  - `artifact_id` (string, format `art_<ULID>`, primary key)
  - `created_at` (datetime)
  - `created_by_principal` (string)
  - `content_type` (string, nullable)
  - `size_bytes` (integer, nullable until completion)
  - `sha256` (hex string, nullable until completion)
  - `storage_backend` (enum: `s3`, `local_fs`)
  - `storage_key` (string, stable key path)
  - `encryption` (enum: `sse-kms`, `sse-s3`, `none`, `envelope`)
  - `status` (enum: `pending_upload`, `complete`, `failed`, `deleted`)
  - `retention_class` (enum: `ephemeral`, `standard`, `long`, `pinned`)
  - `expires_at` (datetime nullable)
  - `redaction_level` (enum: `none`, `preview_only`, `restricted`)
  - `metadata` (json object)
- **Rules**:
  - Artifact IDs are opaque and immutable.
  - Completed artifacts cannot be overwritten.
  - Blob bytes are not stored in Postgres.

## Entity: ArtifactRef

- **Description**: Small JSON-safe reference passed through Temporal workflow/activity boundaries.
- **Fields**:
  - `artifact_ref_v` (integer, currently `1`)
  - `artifact_id` (string)
  - `sha256` (string nullable)
  - `size_bytes` (integer nullable)
  - `content_type` (string nullable)
  - `encryption` (string)
- **Rules**:
  - Workflows store/pass `ArtifactRef` only for non-trivial payloads.
  - Presigned URLs are never stored in workflow state/history.

## Entity: ExecutionRef

- **Description**: Execution linkage used for authorization scope and deterministic listing.
- **Fields**:
  - `namespace` (string)
  - `workflow_id` (string)
  - `run_id` (string)
  - `link_type` (string)
  - `label` (string nullable)
  - `created_by_activity_type` (string nullable)
  - `created_by_worker` (string nullable)
- **Rules**:
  - `namespace`, `workflow_id`, `run_id`, and `link_type` are required for linkage.
  - Linkage is append-only and audit-oriented.

## Entity: ArtifactLink

- **Description**: Relationship from artifact to execution context and machine meaning.
- **Fields**:
  - `id` (UUID primary key)
  - `artifact_id` (foreign key to `Artifact`)
  - `namespace`, `workflow_id`, `run_id` (execution identity)
  - `link_type` (string)
  - `label` (string nullable)
  - `created_at` (datetime)
  - `created_by_activity_type` (string nullable)
  - `created_by_worker` (string nullable)
- **Rules**:
  - Supports deterministic query of latest output by `(namespace, workflow_id, run_id, link_type, created_at)`.
  - Multiple links of same type are valid; latest is a query concern.

## Entity: ArtifactPin

- **Description**: Explicit pin metadata preventing automatic retention deletion.
- **Fields**:
  - `id` (UUID primary key)
  - `artifact_id` (unique foreign key)
  - `pinned_by_principal` (string)
  - `pinned_at` (datetime)
  - `reason` (text nullable)
- **Rules**:
  - At most one active pin row per artifact.
  - Pinning sets retention behavior to `pinned`.

## Entity: ArtifactAccessGrant

- **Description**: Short-lived, scoped transfer grant represented by presign response data.
- **Fields**:
  - `artifact_id` (string)
  - `operation` (enum: `upload`, `upload_part`, `download`)
  - `url` (string)
  - `expires_at` (datetime)
  - `max_size_bytes` (integer nullable)
  - `required_headers` (map nullable)
- **Rules**:
  - Grants are short-lived and method/key scoped.
  - Grant issuance is blocked if authorization fails.

## Entity: ArtifactUploadSession

- **Description**: Upload mode envelope returned by create endpoints.
- **Fields**:
  - `mode` (enum: `single_put`, `multipart`)
  - `artifact_id` (string)
  - `upload_id` (string nullable for multipart)
  - `max_size_bytes` (integer)
  - `expires_at` (datetime)
- **Rules**:
  - `single_put` path is bounded by direct upload threshold.
  - `multipart` path requires completion with part list.

## Entity: ArtifactPreview

- **Description**: Derived preview artifact for redaction-safe UI usage.
- **Fields**:
  - `preview_artifact_id` (string)
  - `preview_of_artifact_id` (string)
  - `policy` (string)
  - `redaction_level` (enum: `preview_only`)
- **Rules**:
  - Preview bytes are generated in activity context.
  - Restricted raw artifacts can require elevated permission while preview remains accessible per policy.

## Entity: LifecycleSweep

- **Description**: One cleanup run that enforces retention and deletion policy.
- **Fields**:
  - `run_id` (string/UUID)
  - `started_at` (datetime)
  - `finished_at` (datetime nullable)
  - `expired_candidate_count` (integer)
  - `soft_deleted_count` (integer)
  - `hard_deleted_count` (integer)
  - `status` (enum: `running`, `complete`, `failed`)
- **Rules**:
  - Sweep must be idempotent and retry-safe.
  - Re-running cleanup must not produce inconsistent state transitions.

## State Transitions

- **Artifact lifecycle**:
  - `pending_upload -> complete` (successful upload completion + integrity checks)
  - `pending_upload -> failed` (integrity mismatch or completion failure)
  - `complete -> deleted` (manual delete or lifecycle soft-delete)
  - `failed -> deleted` (cleanup or explicit delete)
- **Retention transitions**:
  - `standard/ephemeral/long -> pinned` (pin action)
  - `pinned -> standard` (unpin default fallback unless explicitly reassigned)
- **Preview generation**:
  - raw `complete` artifact + preview policy -> new preview artifact in `complete` state

## Authorization and Mode Rules

- `AUTH_PROVIDER=disabled`: user-facing metadata/presign endpoints use default local principal attribution.
- Authenticated modes: principal identity is required and authorization is execution-linked.
- Worker/service identities may operate with least-privilege service principals.

## Determinism and Payload Boundaries

- Workflow payload/history may include `ArtifactRef` and small JSON values only.
- Byte transfer, hashing, preview generation, and storage side effects execute in activities.
