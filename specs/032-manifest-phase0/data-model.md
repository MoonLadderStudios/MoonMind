# Data Model: Manifest Queue Phase 0 Alignment

**Feature**: Manifest Queue Phase 0 Alignment  
**Branch**: `task/20260302/a4bb533a-multi`

## Entity: ManifestQueueJobPayload

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `manifest` | `ManifestEnvelope` | Yes | Canonical manifest metadata persisted in queue payload |
| `manifestHash` | String (`sha256:<digest>`) | Yes | Hash of manifest YAML captured at submission time |
| `manifestVersion` | String (`"v0"`) | Yes | Parsed from YAML `version` |
| `requiredCapabilities` | List[String] | Yes | Server-derived worker capability set |
| `effectiveRunConfig` | Object | Yes | `manifest.run` merged with queue option overrides |
| `manifestSecretRefs` | Object | No | Deduplicated profile/vault references for worker resolution |

### Sub-entity: ManifestEnvelope

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | String | Yes | Must match YAML `metadata.name` |
| `action` | Enum (`plan`, `run`) | Yes | Manifest run action |
| `source` | `ManifestSource` | Yes | Source descriptor for manifest content |
| `options` | Object | No | Optional overrides (`dryRun`, `forceFull`, `maxDocs`) |

### Sub-entity: ManifestSource

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kind` | Enum (`inline`, `registry`, `path`) | Yes | `path` is feature-flagged for dev/test (`allow_manifest_path_source`) |
| `content` | String | For normalization | YAML content used during API normalization |
| `name` | String | Registry source | Registry manifest key |
| `path` | String | Path source | Worker-side path hint when path mode is enabled |
| `contentHash` | String | Yes | Audit copy of normalized hash |
| `version` | String | Yes | Audit copy of normalized version |

## Entity: ManifestRecord (PostgreSQL `manifest` table)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | Integer | PK | Internal identifier |
| `name` | String (unique) | Yes | Registry key |
| `content` | Text | Yes | Stored manifest YAML |
| `content_hash` | String | Yes | Normalized content hash |
| `version` | String | Yes | Manifest schema version |
| `created_at` / `updated_at` | Timestamptz | Yes | Audit timestamps |
| `last_indexed_at` | Timestamptz | No | Legacy compatibility field |
| `last_run_job_id` | UUID | No | Most recent queue job id |
| `last_run_status` | String | No | Most recent queue status |
| `last_run_started_at` / `last_run_finished_at` | Timestamptz | No | Run telemetry |
| `state_json` | JSONB | No | Checkpoint state storage |
| `state_updated_at` | Timestamptz | No | Checkpoint timestamp |

## Response Value Objects

### ManifestValidationErrorResponse

| Field | Type | Description |
|-------|------|-------------|
| `detail.code` | String | `invalid_manifest_job` (queue) or `invalid_manifest` (registry upsert) |
| `detail.message` | String | Actionable manifest contract validation message |

### QueueValidationErrorResponse

| Field | Type | Description |
|-------|------|-------------|
| `detail.code` | String | Existing generic queue code (`invalid_queue_payload`) for non-manifest paths |
| `detail.message` | String | Existing generic queue validation message |

## Capability Derivation Rules (Unchanged)

- Always include `manifest`.
- Embeddings block adds `embeddings` and provider capability (`openai`, `google`, `ollama`).
- Vector store block adds mapped store capability (`qdrant`, `pgvector`, `milvus`).
- Data source types map to capability tokens:
  - `GithubRepositoryReader` → `github`
  - `GoogleDriveReader` → `gdrive`
  - `ConfluenceReader` → `confluence`
  - `SimpleDirectoryReader` → `local_fs`
