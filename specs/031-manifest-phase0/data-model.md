# Data Model: Manifest Task System Phase 0

**Feature**: Manifest Task System Phase 0  
**Branch**: `031-manifest-phase0`

## Entity: ManifestQueueJobPayload (Agent Queue `payload`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `manifest` | `ManifestEnvelope` | ✅ | Canonical manifest metadata persisted with the job |
| `manifestHash` | `sha256:<digest>` string | ✅ | Hash of the YAML captured at submission time |
| `manifestVersion` | Enum (`v0`, `legacy`) | ✅ | Version parsed from YAML (`v0` only for Phase 0) |
| `requiredCapabilities` | List\<string> | ✅ | Server-derived capability tokens used for worker gating |
| `effectiveRunConfig` | Object | ✅ | Manifest `run` block merged with queue overrides (`dryRun`, `forceFull`, `maxDocs`) |

### Sub-entity: ManifestEnvelope

| Field | Type | Required | Rules |
|-------|------|----------|-------|
| `name` | String | ✅ | Must equal `metadata.name` from YAML |
| `action` | Enum (`plan`, `run`) | ✅ | Phase 0 supports `plan` & `run`; `evaluate` rejected |
| `source` | `ManifestSource` | ✅ | How workers obtain YAML (inline or registry) |
| `options` | Object | ❌ | Optional overrides limited to the allowlist |

### Sub-entity: ManifestSource

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `kind` | Enum (`inline`, `registry`) | ✅ | Supported source kinds for Phase 0 |
| `content` | String | Inline only | Raw YAML stored only when `kind="inline"` (never exposed via API responses) |
| `name` | String | Registry only | Registry entry key (defaults to `manifest.name`) |
| `contentHash` | String | ✅ | Mirrors `manifestHash` for worker-side cache validation |
| `version` | String | ✅ | Mirrors `manifestVersion` for worker-safety checks |

## Entity: ManifestRecord (PostgreSQL `manifest` table)

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `id` | Serial PK | ✅ | Internal identifier |
| `name` | String(255), unique | ✅ | Registry key referenced by API routes |
| `content` | Text | ✅ | Latest validated YAML |
| `content_hash` | String(80) | ✅ | `sha256:` hash captured via normalization |
| `version` | String(32) | ✅ | `"v0"` default (FR-011) |
| `created_at` / `updated_at` | timestamptz | ✅ | Audit timestamps added in migration `202602190003` |
| `last_indexed_at` | timestamptz | ❌ | Legacy compatibility |
| `last_run_job_id` | UUID | ❌ | Last queue job generated from this manifest |
| `last_run_status` | String(32) | ❌ | Mirrors queue job status |
| `last_run_started_at` / `last_run_finished_at` | timestamptz | ❌ | Run telemetry for dashboards |
| `state_json` | JSONB | ❌ | Placeholder for checkpoint payloads (Phase 1+) |
| `state_updated_at` | timestamptz | ❌ | Timestamp for the last checkpoint mutation |

## Value Objects

### ManifestRunOptions (API request body)

| Field | Type | Constraints |
|-------|------|-------------|
| `dryRun` | Boolean | Optional |
| `forceFull` | Boolean | Optional |
| `maxDocs` | Integer | Optional, must be ≥ 1 when provided |

### ManifestRunQueueMetadata (API response fragment)

| Field | Type | Description |
|-------|------|-------------|
| `type` | String | Always `"manifest"` |
| `requiredCapabilities` | List\<string> | Derived capability labels returned to clients |
| `manifestHash` | String | Copy of normalized hash for audit comparisons |

## Capability Mapping Rules

| Manifest Field | Mapping Logic | Example Output |
|----------------|--------------|----------------|
| `embeddings.provider` | Always emit `embeddings` plus provider token (`openai`, `google`, `ollama`) | `openai` → `["manifest","embeddings","openai", …]` |
| `vectorStore.type` | Use `VECTOR_STORE_CAPABILITIES` map | `qdrant` → `qdrant` |
| `dataSources[].type` | Case-insensitive map | `GithubRepositoryReader` → `github`, `GoogleDriveReader` → `gdrive`, `ConfluenceReader` → `confluence`, `SimpleDirectoryReader` → `local_fs` |

Unknown adapters/stores cause `ManifestContractError` so unsupported manifests never enter the queue.

## Secret-Safety Contract (FR-007)

- Allowed references:  
  - `${ENV_VAR}` placeholders resolved by worker environments.  
  - `profile://provider#field` profile references.  
  - `vault://<mount>/<path>#<field>` Vault references (same syntax as existing task secret refs).  
- Detection heuristics (non-exhaustive) applied recursively across manifest YAML and queue overrides:  
  - Prefixes: `sk-`, `sk_live_`, `ghp_`, `gho_`, `AIza`, `AKIA`, `ASIA`, `EAAC`, `xoxp-`, `xoxb-`.  
  - Substrings such as `token=`, `secret=`, `password=`, `api_key=` (case-insensitive).  
  - JWT-looking strings (`xxxxx.yyyyy.zzzzz`) and ≥40-character base64-ish blobs without allowable wrappers.  
  - Raw values under known sensitive keys (`auth`, `connection`, `credentials`) unless they are valid vault/profile/env references.  
- Violations raise `ManifestContractError("manifest contains raw secret material")` before the job or registry record is persisted.

## Validation Rules Summary

1. `manifest.name` MUST match `metadata.name` (DOC-REQ-002).  
2. Only version `"v0"` manifests are accepted for Phase 0 (legacy manifests rejected).  
3. `manifest.source.kind` limited to `inline` or `registry`; inline content cannot be blank.  
4. `manifest.options` keys limited to `dryRun`, `forceFull`, `maxDocs`; `maxDocs` ≥ 1 if provided.  
5. Queue payloads MUST include `requiredCapabilities`; worker claims succeed only when their capability set is a superset.  
6. Inline YAML never leaves the server—API serializers always call `sanitize_manifest_payload()` so dashboards see only hashes + metadata.  
7. Secret detection MUST pass for both inline submissions and registry upserts (FR-007).  
8. Registry mutation timestamps + checkpoint columns must be populated via the Alembic migration to unblock future manifest worker phases (FR-011).
