# Data Model: Manifest Phase 0 Rebaseline

**Feature**: `031-manifest-phase0`  
**Branch**: `031-manifest-phase0`

## Entity: Persisted Manifest Queue Payload (`agent_queue.payload`)

This is the normalized payload persisted by `normalize_manifest_job_payload()`.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `manifest` | object | Yes | Canonical envelope for name/action/source/options. |
| `manifestHash` | string (`sha256:<digest>`) | Yes | Hash of source YAML used at submission time. |
| `manifestVersion` | string | Yes | Currently `v0` only for Phase 0 acceptance. |
| `requiredCapabilities` | array<string> | Yes | Server-derived routing labels; must be non-empty for claim eligibility. |
| `effectiveRunConfig` | object | Yes | Manifest `run` block merged with allowed queue option overrides. |
| `manifestSecretRefs` | object | No | Optional deduplicated `profile`/`vault` secret-reference metadata. |

### Sub-entity: `manifest`

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| `name` | string | Yes | Must be non-empty and equal YAML `metadata.name`. |
| `action` | enum | Yes | `plan` or `run` only. |
| `source` | object | Yes | See source-kind model below. |
| `options` | object | Yes (can be empty) | Only `dryRun`, `forceFull`, `maxDocs` allowed. |

### Sub-entity: `manifest.source` (persisted form)

| Kind | Required fields | Persisted fields |
|------|------------------|------------------|
| `inline` | `content` | `kind`, `content`, `contentHash`, `version` |
| `registry` | `name`, `content` (for normalization input) | `kind`, `name`, `contentHash`, `version` (`content` removed before persistence) |
| `path` (guarded) | `path`, `content` | `kind`, `path`, `content`, `contentHash`, `version` |

`path` support is gated by `workflow.allow_manifest_path_source` and defaults to disabled.

## Entity: Sanitized Queue API Payload (`JobModel.payload` for `type="manifest"`)

Queue APIs return `sanitize_manifest_payload(payload)` output rather than the raw persisted payload.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `manifest` | object | Usually | Includes `name`, `action`, `source.kind`, optional `source.name`, optional `source.path`, optional `options`. |
| `manifestHash` | string | No | Present when normalized payload had hash metadata. |
| `manifestVersion` | string | No | Present when normalized payload had version metadata. |
| `requiredCapabilities` | array<string> | No | Lowercased and de-duplicated in serializer. |
| `effectiveRunConfig` | object | No | Copied from normalized payload when present. |
| `manifestSecretRefs` | object | No | Sanitized/deduped profile and vault refs only. |

Notably absent from API responses: `manifest.source.content` and source-level hash/version fields.

## Entity: Manifest Registry Record (`manifest` table)

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `id` | integer PK | Yes | Internal identifier. |
| `name` | string(255), unique | Yes | Registry key and route parameter. |
| `content` | text | Yes | Latest validated manifest YAML. |
| `content_hash` | string(80) | Yes | `sha256:` hash from normalization. |
| `version` | string(32) | Yes | Default and expected current value: `v0`. |
| `created_at` | timestamptz | Yes | Creation timestamp. |
| `updated_at` | timestamptz | Yes | Last update timestamp. |
| `last_run_job_id` | uuid | No | Last queue job submitted from this record. |
| `last_run_status` | string(32) | No | Last known queue status snapshot. |
| `last_run_started_at` | timestamptz | No | Last run start timestamp. |
| `last_run_finished_at` | timestamptz | No | Last run finish timestamp. |
| `state_json` | JSON/JSONB | No | Checkpoint/state payload placeholder for later phases. |
| `state_updated_at` | timestamptz | No | State metadata update timestamp. |
| `last_indexed_at` | timestamptz | No | Legacy compatibility field retained. |

## Value Objects: Manifest Run API

### `ManifestRunOptions`

| Field | Type | Required | Rule |
|-------|------|----------|------|
| `dryRun` | boolean | No | Optional override. |
| `forceFull` | boolean | No | Optional override. |
| `maxDocs` | integer | No | Must be `>= 1` when provided. |

### `ManifestRunResponse.queue`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Always `manifest`. |
| `requiredCapabilities` | array<string> | Yes | Derived capabilities copied from queued payload. |
| `manifestHash` | string | No | Normalized hash from queued payload. |

## Capability Mapping Rules

| Manifest input | Mapping |
|----------------|---------|
| Base capability config | `settings.workflow.manifest_required_capabilities` (default `manifest`) |
| `embeddings.provider` | Adds `embeddings` and provider token (`openai`, `google`, `ollama`) |
| `vectorStore.type` | Adds mapped token (`qdrant`, `pgvector`, `milvus`) |
| `dataSources[].type` | Adds mapped token (`github`, `gdrive`, `confluence`, `local_fs`) |

Unknown provider/store/source types fail normalization with `ManifestContractError`.

## Validation Rules Summary

1. Payload must include `manifest` object with non-empty `manifest.name`.
2. `manifest.action` supports only `plan` and `run`.
3. YAML must parse to an object and declare `version: "v0"`.
4. `manifest.name` must match YAML `metadata.name`.
5. Source kind must be supported (`inline`, `registry`, and guarded `path`).
6. `manifest.options` only supports `dryRun`, `forceFull`, `maxDocs`; invalid keys fail fast.
7. Raw secret-like values are rejected before persistence; env/profile/vault references are allowed.
8. Claim eligibility requires `requiredCapabilities` to be present and to be a subset of worker capabilities.
