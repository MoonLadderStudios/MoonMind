# Data Model: Agent Queue Artifact Upload (Milestone 2)

**Feature**: Agent Queue Artifact Upload  
**Branch**: `010-agent-queue-artifacts`

## Existing Entity (From Milestone 1): AgentJob

Artifact features extend `AgentJob` without changing core lifecycle semantics.

## New Entity: AgentJobArtifact

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | UUID | Yes | Primary key for artifact metadata |
| `job_id` | UUID | Yes | Foreign key to `agent_jobs.id` |
| `name` | String | Yes | Logical artifact name/path relative to job root |
| `content_type` | String | No | MIME type from request or inferred default |
| `size_bytes` | Integer | Yes | Stored payload size |
| `digest` | String | No | Optional checksum supplied or computed |
| `storage_path` | String | Yes | Relative path under configured artifact root |
| `created_at` | Timestamp | Yes | Creation timestamp |
| `updated_at` | Timestamp | Yes | Last metadata update timestamp |

## Supporting Value Object: ArtifactUpload

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | Binary | Yes | Uploaded artifact bytes |
| `name` | String | Yes | Relative artifact path under job root |
| `content_type` | String | No | Client-provided content type |
| `digest` | String | No | Optional integrity hint |

## Relationships

- `AgentJob` (1) -> (N) `AgentJobArtifact`
- Artifact listing and download are always scoped by `job_id`.

## Storage Rules

- Root directory from `AGENT_JOB_ARTIFACT_ROOT` (default `var/artifacts/agent_jobs`).
- Effective artifact path: `<artifact_root>/<job_id>/<name>`.
- `name` must be relative, non-empty, non-absolute, and free of traversal tokens.

## Validation Rules

- Reject artifact names resolving outside job root.
- Reject uploads larger than configured max byte limit.
- Reject download when `artifact_id` is not linked to `job_id`.
- Record metadata only after successful file write.
