# Data Model: Manifest Task System Phase 1

**Feature**: Manifest Task System Phase 1  
**Branch**: `030-manifest-phase1`

## Entity: ManifestV0

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | Enum (`"v0"`) | Yes | Only v0 manifests accepted in Phase 1 (DOC-REQ-001, DOC-REQ-013) |
| `metadata` | Object | Yes | Includes `name`, optional `description`, `owners`; `metadata.name` must match queue payload `manifest.name` |
| `dataSources` | List[`DataSourceSpec`] | Yes | Each entry describes adapter type, id, params, allowlisted metadata, and optional incremental cursor hints |
| `embeddings` | `EmbeddingsSpec` | Yes | Provider/model/dimensions plus batching controls; drives embeddings factory (DOC-REQ-005) |
| `vectorStore` | `VectorStoreSpec` | Yes | Qdrant configuration (host, port/API key ref, `indexName`, `allowCreateCollection`, metric/dim); DOC-REQ-006 |
| `transforms` | `TransformPipeline` | Optional | Specifies `htmlToText`, `splitter`, `enrichMetadata` selections (DOC-REQ-004/007) |
| `run` | `RunConfig` | Optional | Default concurrency/batchSize parameters merged with queue overrides |
| `security` | Object | Optional | Metadata allowlist + secret resolution strategy (env/profile/vault) |

### Sub-entity: DataSourceSpec

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | String | Yes | Stable identifier used in point IDs + checkpoint keys |
| `type` | Enum (`GithubRepositoryReader`, `GoogleDriveReader`, `ConfluenceReader`, `SimpleDirectoryReader`) | Yes | Determines adapter + capability token (DOC-REQ-003) |
| `params` | Object | Yes | Adapter-specific settings (repo path, Drive folder ID, Confluence space/page filters, root directory) |
| `allowlistMetadata` | List[String] | Optional | Additional metadata fields to persist for this source |
| `state` | Object | Optional | Adapter-defined cursor hints saved within checkpoints |

### Sub-entity: EmbeddingsSpec

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | Enum (`openai`, `google`, `ollama`) | Yes | Selects embedding backend + capability token |
| `model` | String | Yes | Provider-specific embedding model |
| `dimensions` | Integer | Optional | Overrides autodetected dimension; validated against Qdrant collection |
| `batchSize` | Integer | Optional | Controls embedding throughput per request |
| `maxConcurrentRequests` | Integer | Optional | Caps concurrency for rate limiting |

### Sub-entity: VectorStoreSpec

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | Enum (`qdrant`) | Yes | Phase 1 scope |
| `indexName` | String | Yes | Qdrant collection name |
| `allowCreateCollection` | Boolean | Optional | Enables dynamic collection creation when absent |
| `connection` | Object | Yes | Env/profile/vault references for Qdrant host/port/API key |
| `distance` | Enum (`cosine`, `dot`, `euclidean`) | Optional | Validated to match manifest embeddings |

### Sub-entity: TransformPipeline

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `htmlToText` | Boolean | Optional | Enables BeautifulSoup stripping before chunking |
| `splitter` | Object | Optional | `TokenTextSplitter` config (`chunkSize`, `chunkOverlap`) |
| `enrichMetadata` | List[String] | Optional | Deterministic enrichments (PathToTags, InferDocType) |

## Value Object: SourceDocument

| Field | Type | Description |
|-------|------|-------------|
| `source_doc_id` | String | Stable, adapter-defined identifier (e.g., `repo/path@sha`) |
| `content` | String | Raw or normalized content (pre-transform) |
| `metadata` | Dict | Adapter-provided safe metadata subject to allowlists |
| `content_hash` | String | Hash used to skip unchanged documents |

## Value Object: SourceChange

| Field | Type | Description |
|-------|------|-------------|
| `kind` | Enum (`upsert`, `delete`) | Determines downstream action |
| `doc` | `SourceDocument` | Present when `kind="upsert"` |
| `source_doc_id` | String | Provided when `kind="delete"` for targeted deletions |

## Entity: ChunkRecord

| Field | Type | Description |
|-------|------|-------------|
| `manifest_name` | String | Provided for namespacing (DOC-REQ-007) |
| `data_source_id` | String | Source identifier |
| `source_doc_id` | String | Stable doc id |
| `chunk_index` | Integer | Sequential chunk number |
| `chunk_text` | String | Post-transform text passed to embeddings |
| `chunk_hash` | String | Hash of chunk_text |
| `doc_hash` | String | Copy of SourceDocument hash for quick filtering |
| `metadata` | Dict | Allowlisted metadata entries only |
| `point_id` | String | Deterministic SHA-256 of manifest/source/doc/chunk/provider/model (DOC-REQ-008) |

## Entity: CheckpointState

| Field | Type | Description |
|-------|------|-------------|
| `manifest_name` | String | Registry key |
| `data_source_states` | Dict[String, DataSourceState] | Per adapter snapshot |
| `updated_at` | Timestamp | UTC timestamp recorded in `state_updated_at` |

### Sub-entity: DataSourceState

| Field | Type | Description |
|-------|------|-------------|
| `cursor` | Dict | Adapter-defined cursor (e.g., Drive page token, Confluence timestamp) |
| `doc_hashes` | Dict[String, String] | Map of `source_doc_id` → `doc_hash` for last successful run |
| `last_run_started_at` | Timestamp | Mirror of registry metadata |
| `last_run_finished_at` | Timestamp | Mirror of registry metadata |

## Entity: StageEventPayload (SSE + queue events)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | Enum (`moonmind.manifest.validate`, `moonmind.manifest.plan`, `moonmind.manifest.fetch`, `moonmind.manifest.transform`, `moonmind.manifest.embed`, `moonmind.manifest.upsert`, `moonmind.manifest.finalize`) | Yes | Stage identifier (DOC-REQ-011) |
| `manifestName` | String | Yes | Manifest identifier |
| `dataSourceId` | String | Optional | Provided for stage-specific metrics |
| `documentsFetched` | Integer | Optional | Stage counters |
| `documentsChanged` | Integer | Optional | Stage counters |
| `documentsDeleted` | Integer | Optional | Stage counters |
| `chunksGenerated` | Integer | Optional | Stage counters |
| `chunksEmbedded` | Integer | Optional | Stage counters |
| `pointsUpserted` | Integer | Optional | Stage counters |
| `pointsDeleted` | Integer | Optional | Stage counters |
| `durationMs` | Integer | Optional | Stage wall clock durations |
| `status` | Enum (`running`, `succeeded`, `failed`, `cancelled`) | Yes | Stage status |
| `artifactPaths` | List[String] | Optional | S3/object-store keys referenced by queue API |

## Artifact Contract

| Artifact Path | Description |
|---------------|-------------|
| `logs/manifest.log` | Streaming worker log (redacted) for the run |
| `manifest/input.yaml` | Submitted manifest YAML (redacted) |
| `manifest/resolved.yaml` | Resolved manifest after interpolation (references only) |
| `reports/plan.json` | Output of `engine.plan` (estimated docs/bytes) |
| `reports/run_summary.json` | Final counts for fetch/transform/embed/upsert/delete |
| `reports/checkpoint.json` | Updated checkpoint state applied to registry |
| `reports/errors.json` | Failure details when run aborts/cancels |

## Validation Rules

1. `ManifestV0` must include at least one `dataSource` and one embeddings/vectorStore block; provider/model + vector dimensions must align before a run can start.
2. `SourceDocument.content_hash` and `ChunkRecord.point_id` must both use SHA-256 to ensure deterministic upsert/delete semantics (DOC-REQ-008).
3. Metadata persisted in Qdrant must pass the allowlist defined either globally (`security.allowlistMetadata`) or per data source; unknown keys are dropped before embedding (DOC-REQ-007).
4. Checkpoint writes only occur after `moonmind.manifest.finalize` reports success; cancellations or failures keep the previous checkpoint intact.
5. Event payloads must redact secrets, emit stage counts in ascending order, and include artifact references only after uploads succeed (DOC-REQ-011/012).
