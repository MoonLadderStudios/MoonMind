# Data Model: Manifest Queue Phase 0

**Feature**: Manifest Queue Phase 0  
**Branch**: `029-manifest-phase0`

## Entity: ManifestQueueJobPayload

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `manifest` | `ManifestEnvelope` | Yes | Canonical manifest metadata persisted in the queue payload (`source.kind`, `options`, run action) |
| `manifestHash` | String (`sha256:<digest>`) | Yes | Hash of the manifest YAML captured at submission time (DOC-REQ-004) |
| `manifestVersion` | String (`"v0"`) | Yes | Parsed from YAML `version`; `"legacy"` rejected for Phase 0 |
| `requiredCapabilities` | List[String] | Yes | Server-derived capability labels used by worker claim filtering |
| `effectiveRunConfig` | Object | Yes | Manifest `run` merged with queue `manifest.options` overrides (dryRun/forceFull/maxDocs only) |

### Sub-entity: ManifestEnvelope

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | String | Yes | Manifest identifier; must match YAML `metadata.name` |
| `action` | Enum (`plan`, `run`) | Yes | Phase 0 supports `plan` + `run` per §6.4 |
| `source` | `ManifestSource` | Yes | Indicates how the worker retrieves YAML (`inline` vs `registry`) |
| `options` | Object | No | Optional overrides limited to `dryRun`, `forceFull`, `maxDocs` |

### Sub-entity: ManifestSource

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kind` | Enum (`inline`, `registry`) | Yes | Phase 0 source kinds |
| `content` | String | Inline only | Raw YAML stored only when `kind=="inline"`; stripped from API responses |
| `name` | String | Registry only | Registry entry name (defaults to `manifest.name`) |
| `contentHash` | String | Yes | Duplicates `manifestHash` for audit and worker cache validation |
| `version` | String | Yes | Mirrors `manifestVersion` for quick lookups by workers |

## Entity: ManifestRecord (PostgreSQL `manifest` table)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | Integer | PK | Internal identifier |
| `name` | String (unique) | Yes | Registry key referenced by `/api/manifests/{name}` |
| `content` | Text | Yes | Latest YAML definition (validated + normalized) |
| `content_hash` | String | Yes | Result of manifest normalization (DOC-REQ-004) |
| `version` | String | Yes | `"v0"` for compliant manifests; server default |
| `created_at` / `updated_at` | Timestamptz | Yes | Audit timestamps |
| `last_indexed_at` | Timestamptz | No | Legacy runner compatibility hook |
| `last_run_job_id` | UUID | No | Most recent queue job id triggered from registry |
| `last_run_status` | String | No | Queue status of `last_run_job_id` |
| `last_run_started_at` / `last_run_finished_at` | Timestamptz | No | Run telemetry for dashboards |
| `state_json` | JSONB | No | Checkpoint payload for incremental sync state (§8.11) |
| `state_updated_at` | Timestamptz | No | Timestamp for the last checkpoint write |

## Value Object: ManifestRunOptions (API Request)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `dryRun` | Boolean | No | Forces validation/plan-only run |
| `forceFull` | Boolean | No | Forces full re-fetch irrespective of checkpoints |
| `maxDocs` | Integer (>=1) | No | Caps number of documents processed per run |

## Response Model: ManifestRunQueueMetadata

| Field | Type | Description |
|-------|------|-------------|
| `type` | String | Always `"manifest"` |
| `requiredCapabilities` | List[String] | Capabilities derived at submission (manifest/embeddings/provider/vectorStore/sources) |
| `manifestHash` | String | Copy of normalized hash for client-side audit |

## Capability Derivation Rules

- `manifest` is always included.
- Embeddings block (`embeddings.provider`) adds `embeddings` + provider label: `openai`, `google`, `ollama`.
- Vector store block adds the store capability per `VECTOR_STORE_CAPABILITIES` map (Phase 0 focuses on `qdrant` but keeps future stores enumerable).
- Each `dataSources[].type` maps (case-insensitive) to capability tokens:
  - `GithubRepositoryReader` → `github`
  - `GoogleDriveReader` → `gdrive`
  - `ConfluenceReader` → `confluence`
  - `SimpleDirectoryReader` → `local_fs`
- Unknown adapters raise `ManifestContractError` so unsupported integrations cannot be queued silently.

## Validation Rules

- `manifest.name` must equal `metadata.name` from YAML (§6.2.2).
- Only `version: "v0"` manifests are accepted (Phase 0 scope).
- `manifest.source.kind` limited to `inline` or `registry`; inline content must be non-empty YAML.
- `manifest.options` keys restricted to the allowlist; `maxDocs` must be ≥ 1 when provided (§6.2.3).
- Queue payloads must include `requiredCapabilities`; `AgentQueueRepository._is_job_claim_eligible` rejects jobs missing this array or any worker that lacks the full set (DOC-REQ-001).
- API serializers must strip `manifest.source.content` before returning queue jobs to avoid leaking YAML (FR-009).
