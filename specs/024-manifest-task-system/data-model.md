# Data Model: Manifest Task System

## Manifest (v0)
- **Purpose**: Defines ingestion sources, transforms, embedding config, and vector store target.
- **Fields**:
  - `metadata.name` (string, required): Manifest identifier.
  - `metadata.version` (enum): `v0` for new manifests, `legacy` for backward compatibility.
  - `dataSources[]` (array): Each entry includes `type`, connector-specific config, and optional capability tags.
  - `transforms` (object): `htmlToText` flag, `splitter` settings, `enrichMetadata` rules.
  - `embeddings` (object): Provider (`openai`, `google`, `ollama`), model name, dimension.
  - `vectorStore` (object): `type`, `indexName`, `allowCreateCollection`, expected distance metric.
  - `security.allowlistMetadata` (array): Permitted metadata keys for upserts.

## ManifestJobPayload
- **Purpose**: Agent Queue payload for manifest jobs.
- **Fields**:
  - `requiredCapabilities[]`: Server-derived capabilities (at minimum `manifest`, `embeddings`, vector store + source connectors).
  - `manifest` (object): Includes `name`, `action` (`plan` or `run` phase 1), `source`, and `options` (`dryRun`, `forceFull`, `maxDocs`).
  - `source.kind`: `inline`, `path`, `registry`, or `repo`; Phase 1 supports inline + path.

## Manifest Run (Agent Queue Job)
- **Purpose**: Tracks lifecycle of a manifest ingestion execution.
- **Fields**:
  - `job_id` (UUID): Primary key within `agent_queue_jobs`.
  - `type` (string): `manifest`.
  - `status` (enum): pending → running → succeeded/failed/cancelled.
  - `requiredCapabilities[]`: Stored for audit.
  - `events[]`: Stage events (`validate`, `plan`, `fetch`, `transform`, `embed`, `upsert`, `finalize`) with timings/counts.
  - `artifacts[]`: `logs/manifest.log`, `manifest/input.yaml`, `manifest/resolved.yaml`, `reports/*.json`.

## Manifest Worker Registration
- **Purpose**: Worker metadata for claim filtering.
- **Fields**:
  - `worker_id`: Unique identifier with heartbeat.
  - `capabilities[]`: Must include `manifest` plus connectors (e.g., `github`, `gdrive`).
  - `lease_seconds`, `poll_interval_ms`: Control claim cadence.
  - `tokens`: `MOONMIND_WORKER_TOKEN` referencing server-trusted secrets, not stored in payloads.

## Manifest Registry (Postgres `manifest` table)
- **Existing columns**:
  - `id` (integer, PK)
  - `name` (string, unique): Manifest identifier.
  - `content` (text): Stored manifest body.
  - `content_hash` (string): Content hash for change detection.
  - `last_indexed_at` (timestamptz, nullable): Last successful indexing timestamp.
- **Proposed additive columns**:
  - `version` (string, nullable): `v0` or `legacy`.
  - `updated_at` (timestamptz): Last write timestamp.
  - `last_run_job_id` (UUID, nullable): Most recent queue job.
  - `last_run_status` (string, nullable): Job outcome for dashboard linking.

## Tasks Dashboard Category Metadata
- **Fields**:
  - `category` (string): `Manifests` (new tab/filter).
  - `submitForm`: { `manifestName`, `manifestSource`, `action`, `dryRun`, `priority` }.
  - `detailView`: Reuses queue job detail (SSE events, artifacts) filtered by `type="manifest"`.
