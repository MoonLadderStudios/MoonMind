# Manifest Task System (Ingest Manifests via Agent Queue)

Status: Draft (implementation-ready)
Owners: MoonMind Engineering
Last Updated: 2026-02-18

## 1. Purpose

Define how MoonMind ingests **manifest-defined data pipelines** using the existing **Agent Queue** (`/api/queue`) so that:

- Manifest ingestion runs are submitted, monitored, cancelled, and audited like other queue jobs.
- The Tasks Dashboard UI can show manifest runs as a first-class **category** (separate from codex/gemini/claude).
- Ingestion is deterministic and declarative: **validate → fetch → transform → embed → upsert (+ delete)**.
- Ingestion is observable (events + artifacts) and safe (no raw secrets in payloads/logs).

This design intentionally reuses:
- Agent Queue job lifecycle, events, artifacts, cancellation, and worker token policy.
- Existing v0 manifest foundations (YAML schema, interpolation patterns, operator docs).
- “Worker direct data plane” guidance (Qdrant + embeddings) per `docs/WorkerVectorEmbedding.md`.

## 2. Background / Current Repo State

### 2.1 What exists today
- Legacy manifest schema (`apiVersion/kind/spec.readers`) in `moonmind/schemas/manifest_models.py`.
- Loader + interpolation + runner in `moonmind/manifest/*`.
- DB table `manifest` (`ManifestRecord`) + `ManifestSyncService` (hash detect + run readers) but no indexing pipeline.
- “v0 manifest” examples and operator guide in `docs/LlamaIndexManifestSystem.md` + `examples/readers-*.yaml`.

### 2.2 What is missing
- No Agent Queue job type for manifest ingestion.
- No dedicated manifest ingestion worker.
- No v0 execution engine that performs: validate → fetch → transform → embed → upsert (+ delete).
- No UI category/submit flow dedicated to manifests.
- No consistent, token-free secret resolution story for manifests (env/profile/vault references only).
- No explicit idempotency/deletion rules (required for real “sync”, not just “append”).

## 3. Goals and Non-Goals

### 3.1 Goals
1. Add a **new queue job type** for manifest ingestion runs.
2. Provide a **manifest worker** (`moonmind-manifest-worker`) that:
   - claims manifest jobs
   - streams progress via queue events
   - uploads artifacts (logs/reports)
   - supports cancellation
3. Support **v0 manifests** as defined in `docs/LlamaIndexManifestSystem.md` and `examples/readers-*.yaml`.
4. Keep queue payloads **token-free**: only allow env references, profile references, and/or secret references (Vault) — never raw API keys.
5. Make manifest runs visible in the Tasks Dashboard UI as a **separate category** from codex/gemini/claude.
6. Make runs **idempotent**:
   - stable point IDs
   - incremental updates
   - deletions (when documents disappear or are replaced)
7. Add **checkpointing** so incremental sync is possible and resumable.

### 3.2 Non-Goals (for this document)
- Implementing every v0 feature (hybrid retrieval, rerankers, full evaluation suite) in the first increment.
- Full multi-tenant isolation and fine-grained ACL enforcement on vector indices (can be layered later).
- Replacing existing `/v1/documents/*` ingestion endpoints immediately.

## 4. Key Concepts

**Manifest (v0)**
A YAML document describing ingestion sources, transforms, embeddings, vector store target, and optional retrieval configuration.

**Manifest Run**
A single execution of a manifest. Represented as an Agent Queue job of type `manifest`.

**Manifest Worker**
A daemon that claims manifest jobs and executes ingestion pipelines (no LLM runtime selection).

**Control Plane vs Data Plane**
- Control plane: queue job creation/claim/status, events, artifacts (FastAPI).
- Data plane: embeddings + vector store upserts/deletes (worker direct to Qdrant; optionally API-mediated later).

**Source Document ID**
A stable identifier for a logical document in a source system (e.g., Confluence page ID, Drive file ID, GitHub path@ref).

**Point ID**
A stable identifier for a vector entry (a chunk/node) in Qdrant. Must be deterministic to support upsert and deletion.

**Checkpoint**
Per-(manifest,dataSource) state capturing what was last indexed so subsequent runs can be incremental.

## 5. High-Level Architecture

```mermaid
flowchart LR
  UI[Tasks Dashboard UI] --> API[FastAPI /api/queue + /api/manifests]
  API --> Q[(Postgres: agent_queue tables + manifest registry)]
  API -->|Create job type=manifest| Q
  W[moonmind-manifest-worker] -->|claim/heartbeat/events/artifacts| API
  W -->|embed| Emb[Embedding Provider]
  W -->|upsert/delete/query| VDB[(Qdrant)]
  W -->|checkpoint + last_run updates| Q
````

## 6. Queue Job Type and Payload Contract

### 6.1 New job type

Add a new Agent Queue job type:

* `type = "manifest"`

This job type is **not** executed by codex/gemini/claude workers.
It is executed only by workers advertising capability `manifest`.

#### 6.1.1 Implementation note (current Agent Queue enforcement)

Agent Queue currently enforces an allowlist of job types inside `moonmind/workflows/agent_queue/service.py` (via `_SUPPORTED_QUEUE_JOB_TYPES`).
To register the manifest job type, update this allowlist so `manifest` is accepted.

Recommended: create a dedicated module for job types (to avoid coupling manifest support to task-only constants), e.g.:

* `moonmind/workflows/agent_queue/job_types.py`

  * `SUPPORTED_QUEUE_JOB_TYPES = {"task", "codex_exec", "codex_skill", "manifest"}`

Then have `service.py` import that set.

### 6.2 Canonical payload: ManifestJobPayload (client → API → persisted)

Clients submit only the manifest object. The API validates/parses the manifest and derives
`requiredCapabilities` server-side before persisting the job so claim filtering works.

Client request payload:

```json
{
  "manifest": {
    "name": "confluence-eng",
    "action": "run",
    "source": {
      "kind": "inline",
      "content": "version: \"v0\"\nmetadata:\n  name: confluence-eng\n..."
    },
    "options": {
      "dryRun": false,
      "forceFull": false,
      "maxDocs": null
    }
  }
}
```

Persisted queue payload (after API derivation + normalization):

```json
{
  "requiredCapabilities": ["manifest", "qdrant", "embeddings", "confluence", "google"],
  "manifest": {
    "name": "confluence-eng",
    "action": "run",
    "source": {
      "kind": "inline",
      "content": "version: \"v0\"\nmetadata:\n  name: confluence-eng\n..."
    },
    "options": {
      "dryRun": false,
      "forceFull": false,
      "maxDocs": null
    }
  }
}
```

#### 6.2.1 Contract module (do not overload task_contract)

Create a dedicated contract module for manifest queue payloads:

* `moonmind/workflows/agent_queue/manifest_contract.py`

  * `ManifestContractError(ValueError)`
  * `normalize_manifest_job_payload(payload: Mapping[str, Any]) -> dict[str, Any]`
  * `derive_required_capabilities(manifest: ManifestV0) -> list[str]`

Then update `AgentQueueService.create_job()` to normalize like:

* if `type == "task"`: existing task normalization
* if `type == "manifest"`: manifest normalization
* else: existing legacy normalization

This prevents task-only validation rules (e.g., vault secret ref parsing for task auth) from leaking into manifest payload processing.

#### 6.2.2 Required name consistency rule

To avoid ambiguity and guarantee stable namespacing:

* `payload.manifest.name` MUST equal `manifest_yaml.metadata.name`.

If they differ, the API rejects the job creation request with a clear validation error.

#### 6.2.3 Options precedence

v0 manifests include a `run:` block (concurrency/batchSize/etc). Queue payload also includes `manifest.options`.

Rules:

* `manifest.options` MAY override run-control fields only:

  * `dryRun`, `forceFull`, `maxDocs`
* `manifest.options` MUST NOT override structural fields:

  * `dataSources`, `embeddings`, `vectorStore`, `indices`, `retrievers`, `security`
* Effective run config is:

  * `effective = manifest_yaml.run` merged with `manifest.options` overrides.

### 6.3 Manifest source kinds

`manifest.source.kind` determines how the worker obtains the YAML:

* `inline`: YAML embedded directly in payload (`content`).
* `registry`: reference by name (`manifest.name`), worker fetches content from manifest registry (`GET /api/manifests/{name}`).
* `path`: worker reads file from local filesystem path (used in dev/CI images containing manifests).
* `repo`: worker clones a repo/path and reads YAML (optional; requires git + auth).

Phase-1 SHOULD support `inline` and `registry` (registry enables reuse/governance).
`path` is acceptable for dev/test.
`repo` is optional and can be added later.

### 6.4 Actions

* `plan`: validate + estimate counts/costs; no upserts/deletes.
* `run`: full ingestion (fetch → transform → embed → upsert/delete).
* `evaluate`: optional; run retrieval eval dataset (future phase).

### 6.5 Capability derivation (server-side)

On job creation, API MUST parse/validate manifest and derive `requiredCapabilities`, at minimum:

* Always include: `manifest`
* Include vector store capability based on `vectorStore.type`:

  * `qdrant` → `qdrant`
* Include embedding capability:

  * always include `embeddings`
  * optionally include provider capability: `openai` / `google` / `ollama`
* Include source capabilities based on `dataSources[].type`:

  * `GithubRepositoryReader` → `github`
  * `GoogleDriveReader` → `gdrive`
  * `ConfluenceReader` → `confluence`
  * `SimpleDirectoryReader` → `local_fs` (or omit if always available)

Workers must advertise a superset of required capabilities or they will never claim the job.

### 6.6 API-side normalization responsibilities

When creating a `manifest` job, the API MUST also compute and store:

* `payload.manifestHash`: hash of the input YAML content (or registry content at submission time)
* `payload.manifestVersion`: `"v0"` or `"legacy"` (phase-1 supports v0; legacy is optional)
* `payload.requiredCapabilities`: derived as above (client-supplied values are ignored/overwritten)
* `payload.manifestSecretRefs`: optional map describing deduplicated secret references found in the manifest. The API emits:

  * `profile`: list of `{provider, field, envKey, normalized}` entries so workers know which profile/env keys to request.
  * `vault`: list of `{mount, path, field, ref}` entries the worker can pass directly to its Vault resolver.

These fields improve auditability and allow the worker to detect “registry changed mid-run”.

## 7. Manifest Registry (CRUD + Run Submission)

### 7.1 DB model

Reuse existing `manifest` table (`ManifestRecord`) and extend as needed.

Existing columns today:

* `id` (integer PK)
* `name` (string, unique)
* `content` (text manifest body)
* `content_hash` (string hash)
* `last_indexed_at` (nullable timestamp)

Additive columns (backward compatible):

* `version` (nullable string; `"v0"` or `"legacy"`)
* `updated_at` (timestamp)
* `last_run_job_id` (nullable UUID)
* `last_run_status` (nullable string)
* `last_run_started_at` / `last_run_finished_at` (nullable timestamp)
* `state_json` (nullable jsonb): per-manifest checkpoint state (see Section 8.10)
* `state_updated_at` (nullable timestamp)

Checkpoint state can be stored either as:

* a single `state_json` for the whole manifest (containing per-dataSource sub-objects), or
* a new `manifest_state` table keyed by `(manifest_name, data_source_id)` (preferred if state grows large).

### 7.2 API endpoints (recommended)

Add a small CRUD surface:

* `GET /api/manifests` → list manifests
* `GET /api/manifests/{name}` → get YAML + metadata
* `PUT /api/manifests/{name}` → upsert YAML (validates and stores hash)
* `POST /api/manifests/{name}/runs` → submits queue job `type="manifest"` referencing registry

Submission returns the queue job id for dashboard navigation.

## 8. Manifest Execution Engine (v0, declarative)

### 8.1 Package layout

Create a dedicated v0 engine (do not reuse legacy “manifest runner” directly):

* `moonmind/manifest_v0/models.py` (Pydantic models)
* `moonmind/manifest_v0/yaml_io.py` (load/dump helpers)
* `moonmind/manifest_v0/validator.py` (schema + semantic validation)
* `moonmind/manifest_v0/interpolate.py` (env + secret ref + profile ref resolution)
* `moonmind/manifest_v0/secret_refs.py` (vault/profile ref parsing + redaction helpers)
* `moonmind/manifest_v0/readers/*` (ReaderAdapters)
* `moonmind/manifest_v0/transforms/*` (html/text, splitting, metadata enrich)
* `moonmind/manifest_v0/embeddings_factory.py` (build embed model from manifest)
* `moonmind/manifest_v0/vector_store_factory.py` (build qdrant client/store from manifest)
* `moonmind/manifest_v0/id_policy.py` (stable point-id construction)
* `moonmind/manifest_v0/state_store.py` (checkpoint read/write)
* `moonmind/manifest_v0/engine.py` (plan/run orchestration + stage reporting)
* `moonmind/manifest_v0/reports.py` (plan.json/run_summary.json)

Keep legacy support in `moonmind/manifest/*` unchanged.

### 8.2 Engine inputs/outputs

Inputs:

* `ManifestV0` (parsed + validated)
* `effective_run_config` (manifest.run + queue overrides)
* `checkpoint_state` (from registry)

Outputs:

* Upserts/deletes to Qdrant
* Updated checkpoint state persisted back to registry
* Artifacts + events describing what happened

### 8.3 ReaderAdapter interface (change-aware)

To support idempotency and deletions, adapters must be able to emit “upsert” vs “delete” signals.

```python
from dataclasses import dataclass
from typing import Iterable, Protocol, Literal, Optional

@dataclass(frozen=True)
class SourceDocument:
    source_doc_id: str          # stable ID within the source system
    content: str                # raw or normalized body
    metadata: dict              # safe metadata only (before allowlist filtering)
    content_hash: str           # stable hash of content used to skip unchanged docs

@dataclass(frozen=True)
class SourceChange:
    kind: Literal["upsert", "delete"]
    doc: Optional[SourceDocument] = None
    source_doc_id: Optional[str] = None

@dataclass(frozen=True)
class PlanStats:
    estimated_documents: int | None
    estimated_bytes: int | None
    notes: list[str]

class ReaderAdapter(Protocol):
    def plan(self, *, state: dict) -> PlanStats: ...
    def fetch_changes(self, *, state: dict, max_docs: int | None) -> Iterable[SourceChange]: ...
    def checkpoint(self) -> dict: ...
```

Notes:

* Adapters MAY implement incremental fetch based on `state` and update their internal checkpoint as they fetch.
* `checkpoint()` returns updated state to persist after a successful run.
* For sources that cannot cheaply provide deletions incrementally, the engine can do periodic “full enumerate + compare” runs (triggered by `forceFull`).

### 8.4 Initial adapters to implement (final pipeline set)

Implement these adapters first (matches your immediate integrations goal):

* `GithubRepositoryReaderAdapter`
* `GoogleDriveReaderAdapter`
* `ConfluenceReaderAdapter`
* `SimpleDirectoryReaderAdapter`

Capability mapping must match Section 6.5.

### 8.5 Transforms (v0 baseline)

Baseline transforms:

* `htmlToText: true|false`

  * If true, strip HTML markup and store plain text (Confluence/Drive exports often contain HTML).
* `splitter`:

  * `TokenTextSplitter` with `chunkSize`, `chunkOverlap`
* `enrichMetadata` (optional, safe-by-default):

  * `PathToTags`
  * `InferDocType`

Transform rules:

* All transform outputs MUST be deterministic.
* Transforms MUST NOT introduce secrets into metadata or content.

### 8.6 Chunking → Nodes

The engine converts each `SourceDocument` into N chunks (“nodes”), each with:

* `source_doc_id` (stable)
* `chunk_index` (0..N-1)
* `chunk_text`
* metadata (after allowlist filtering; see Section 8.9)

The engine SHOULD also compute:

* `chunk_hash = sha256(chunk_text)` (or equivalent)
* `doc_hash = source_doc.content_hash`

These hashes enable:

* skipping unchanged documents
* deleting/replacing all chunks for a changed document

### 8.7 Embeddings (manifest-driven)

Embeddings MUST be built from the manifest’s `embeddings` block, not only from global settings.

Implement:

* `moonmind/manifest_v0/embeddings_factory.py` that accepts:

  * provider (`google` / `openai` / `ollama`)
  * model name
  * optional explicit `dimensions` (or resolve via provider defaults)
  * batching controls (`batchSize`, `maxConcurrentRequests`)

Key invariants:

* Embedding dimension MUST match the target Qdrant collection.
* Indexing MUST set `Settings.llm = None` (embedding-only pipeline).
* Batch embedding MUST emit progress events (counts/timings), never raw content.

### 8.8 Vector store (Qdrant, manifest-driven)

Phase-1 supports:

* `vectorStore.type = "qdrant"`

Collection selection:

* `vectorStore.indexName` is REQUIRED and is the Qdrant collection name for this manifest.

Collection provisioning:

* If collection missing:

  * If `vectorStore.allowCreateCollection=true`, create it with expected dims + distance metric (default cosine).
  * Else fail with a clear error.

Connection:

* `vectorStore.connection` is resolved from env/profile/vault references, never raw secrets in queue payload.

### 8.9 Namespacing and metadata allowlist

Every upserted point MUST include:

* `manifest.name`
* `dataSource.id`
* `source_doc_id`
* `doc_hash`
* `chunk_index`
* safe source metadata (path/repo/branch/docType/etc), filtered by allowlist

Apply `security.allowlistMetadata` to restrict stored metadata fields.

Rule of thumb:

* If it’s not explicitly allowlisted, do not store it in Qdrant payload.

### 8.10 Idempotency, upserts, and deletions (required)

#### 8.10.1 Stable point IDs (required)

Define point IDs deterministically so repeated runs overwrite the same points:

```
point_id = sha256(
  manifest_name + "|" +
  data_source_id + "|" +
  source_doc_id + "|" +
  str(chunk_index) + "|" +
  embeddings_provider + "|" +
  embeddings_model
)
```

Rationale:

* Including embedding provider/model prevents collisions when re-indexing the same docs into the same collection with a different embedding configuration.

#### 8.10.2 Upsert semantics

For each `SourceChange(kind="upsert")`:

* If `forceFull=false` AND `doc_hash` matches last known hash (from checkpoint), the engine MAY skip embedding/upsert entirely.
* Otherwise:

  1. delete all existing points for `(manifest.name, dataSource.id, source_doc_id)` (delete-by-filter)
  2. compute new chunks
  3. embed + upsert new points

This “delete then upsert” avoids stale chunks when a document shrinks/re-splits.

#### 8.10.3 Delete semantics

For each `SourceChange(kind="delete")` (or detected disappearance in a full run):

* delete all points for `(manifest.name, dataSource.id, source_doc_id)` via Qdrant filter.

The engine MUST record deletion counts in run summary artifacts.

### 8.11 Checkpointing and state persistence

Checkpoint state is per `(manifest.name, dataSource.id)` and MUST include:

* last successful run timestamp
* last processed cursor (if the source supports it)
* map of `source_doc_id -> doc_hash` (or a compact bloom/rolling strategy if needed later)

Persistence:

* Read state at run start from `ManifestRecord.state_json` (or `manifest_state` table).
* Write updated state at run success.
* Optionally write “intermediate checkpoints” between stages to support crash recovery (especially during large runs).

## 9. Manifest Worker

### 9.1 New worker service

Add a new worker daemon similar to `moonmind-codex-worker`:

* Entry point: `poetry run moonmind-manifest-worker`
* Module: `moonmind/agents/manifest_worker/worker.py`

This worker:

* claims jobs with `type="manifest"`
* runs the v0 engine
* talks to embeddings providers + Qdrant directly (data plane)
* reports progress + artifacts via queue APIs (control plane)

### 9.2 Worker configuration (env)

Minimum env vars:

* `MOONMIND_URL`
* `MOONMIND_WORKER_ID`
* `MOONMIND_WORKER_TOKEN`
* `MOONMIND_WORKDIR` (workspace root)
* `MOONMIND_LEASE_SECONDS`, `MOONMIND_POLL_INTERVAL_MS`
* `MOONMIND_WORKER_ALLOWED_TYPES=manifest`
* `MOONMIND_WORKER_CAPABILITIES=manifest,qdrant,embeddings,confluence,github,gdrive,...`

Embedding provider keys (resolved via env/profile/vault):

* `GOOGLE_API_KEY` / `GEMINI_API_KEY`
* `OPENAI_API_KEY`
* `OLLAMA_BASE_URL` (if used)

Qdrant:

* `QDRANT_HOST`, `QDRANT_PORT` (or `QDRANT_URL`)
* optional `QDRANT_API_KEY`

### 9.2.1 Worker preflight (required)

On startup (and before first run), the manifest worker SHOULD validate:

* Qdrant is reachable
* required collections exist or can be created (if allowed)
* embedding provider configuration is present for the providers the worker claims
* if a job is claimed:

  * embedding dimension matches the target collection vector size
  * collection distance metric matches expected (cosine by default)

### 9.3 Stage plan + events

Manifest jobs emit stage events analogous to task stages:

1. `moonmind.manifest.validate`
2. `moonmind.manifest.plan` (optional when action=run; required when action=plan)
3. `moonmind.manifest.fetch`
4. `moonmind.manifest.transform`
5. `moonmind.manifest.embed`
6. `moonmind.manifest.upsert` (includes deletes)
7. `moonmind.manifest.finalize`

Each stage emits:

* started/finished/failed
* counts/timings in payload (never secrets)

Recommended event payload fields:

* `manifestName`, `dataSourceId`
* `documentsFetched`, `documentsChanged`, `documentsDeleted`
* `chunksGenerated`, `chunksEmbedded`, `pointsUpserted`, `pointsDeleted`
* `durationMs`

### 9.4 Required artifacts

Worker uploads artifacts to the queue artifact store:

* `logs/manifest.log`
* `manifest/input.yaml` (original YAML, redacted if needed)
* `manifest/resolved.yaml` (after interpolation, secrets redacted)
* `reports/plan.json`
* `reports/run_summary.json`
* `reports/checkpoint.json` (final persisted checkpoint state)
* `reports/errors.json` (if failed)

### 9.5 Cancellation

Worker must honor queue cancellation requests using the same mechanism as codex worker:

* heartbeat loop observes `cancelRequestedAt`
* worker stops at safe boundaries (between stages, or between batches) and acknowledges cancellation with `/cancel/ack`

Cancellation policy:

* If cancellation occurs mid-upsert batch, the worker SHOULD finish the current Qdrant request and then stop.
* If partial results were written, the run_summary must reflect partial counts.

## 10. Tasks Dashboard UI Integration

### 10.1 New category

Add a new dashboard category:

* **Manifests** (Agent Queue jobs where `type="manifest"`) surfaced at `/tasks/manifests`.

This category is distinct from runtime selection (codex/gemini/claude).

### 10.2 Submit form

Add `/tasks/queue/new-manifest` (or extend existing submit form with a type switch).

Fields:

* Manifest Name
* Manifest Source:

  * Inline YAML editor
  * Select from registry list
* Action: `plan` | `run`
* Dry run checkbox
* Force full checkbox
* Max docs (optional)
* Priority

The dashboard implementation exposes `/tasks/manifests/new`, which posts to `/api/queue/jobs` with `type="manifest"`. Inline submissions paste YAML directly; registry mode prompts for the stored manifest name and reuses `/api/manifests/{name}` content.

UI submits:

* `POST /api/queue/jobs` with `type="manifest"` and payload per Section 6

  * For registry submission: prefer `POST /api/manifests/{name}/runs` which creates the queue job.

### 10.3 Detail view

Reuse existing queue detail page:

* SSE event stream shows stage progress
* artifacts list provides run reports
* show derived `requiredCapabilities` and `manifestHash`

## 11. Security Model

### 11.1 No raw secrets in payloads

Queue payload MUST NOT contain raw keys/tokens.

Allowed reference patterns:

* `${ENV_VAR}` references (resolved by worker runtime env)
* Profile references (resolved by API/worker without storing raw secrets in payload):

  * `profile://<provider>#<field>` (e.g., `profile://openai#api_key`)
* Vault secret references:

  * `vault://<mount>/<path>#<field>`

### 11.2 Secret resolution modes

Support these modes (in order of operational maturity):

1. **Env mode (fast path)**

* Worker reads provider keys from environment variables.
* Works well for single-tenant/dev environments.

2. **Profile mode (recommended bridge)**

* API stores encrypted provider keys in the user profile database.
* When submitting a run, the system chooses a resolution strategy:

  * registry submission can specify “use my profile”
  * worker can fetch a short-lived token or resolved credentials via API (control plane)
* Queue payload remains token-free.
* Worker tokens that advertise the `manifest` capability may call `POST /api/queue/jobs/{jobId}/manifest/secrets` to resolve profile-backed credentials. The response echoes the sanitized `manifestSecretRefs.profile` entries along with resolved `value` fields (redacted in logs) while simply passing through Vault references so the worker can contact Vault directly.

3. **Vault mode (hardening)**

* Manifest contains only `vault://...` references.
* Worker resolves via Vault client (reuse patterns from existing secret-ref handling).

### 11.3 Logging and artifact redaction

Worker must redact token-like strings from:

* events
* logs
* artifacts

Additionally:

* `manifest/resolved.yaml` MUST be redacted (no resolved raw secrets).
* If a source requires auth and the manifest uses env/profile/vault refs, store only the reference string in resolved output.

## 12. Delivery Plan (straight to final declarative pipeline)

### Phase 0: Queue plumbing

1. Register `manifest` as a supported queue job type (Agent Queue allowlist).
2. Add `manifest_contract.py` + API-side normalization/capability derivation for manifest jobs.
3. Add minimal `/api/manifests` registry endpoints (GET/PUT) and `/runs` submission.

### Phase 1: Final v0 execution engine + worker

1. Implement `moonmind/manifest_v0/*` engine (Sections 8.x), including:

   * adapters (GitHub, Drive, Confluence, Local FS)
   * deterministic transforms + splitting
   * embeddings_factory + vector_store_factory
   * stable IDs + delete-by-filter semantics
   * checkpoint persistence
2. Implement `moonmind-manifest-worker` with:

   * preflight checks
   * stage events
   * artifacts contract
   * cancellation

### Phase 2: UI + operational hardening

1. Add Tasks Dashboard category + submit form for manifests.
2. Add “profile mode” secret resolution for registry-based runs (token-free queue payload).
3. Add Vault support for hardened deployments.

### Phase 3: Retrieval profiles + eval (optional)

1. Wire `retrievers` from v0 manifests into query-time selection (retriever profiles).
2. Add evaluation action + datasets.
3. Expand transforms/postprocessors and add Jira adapter.

## 13. Related Documents

* `docs/LlamaIndexManifestSystem.md`
* `docs/TaskArchitecture.md`
* `docs/TaskUiArchitecture.md`
* `docs/WorkerVectorEmbedding.md`
* `docs/WorkerGitAuth.md`
* `specs/024-manifest-task-system/contracts/manifests-api.md`
