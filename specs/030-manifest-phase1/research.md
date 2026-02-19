# Phase 0: Research Findings

**Feature**: Manifest Task System Phase 1  
**Branch**: `030-manifest-phase1`  
**Date**: February 19, 2026

## ReaderAdapter + Source Contracts

### Decision
Implement a new `moonmind/manifest_v0/readers/base.py` that defines the `ReaderAdapter` Protocol plus `SourceDocument`, `SourceChange`, and `PlanStats` dataclasses exactly as specified in docs/ManifestTaskSystem §8.3. Each concrete adapter (GitHub, Google Drive, Confluence, Simple Directory) will inherit from a shared `BaseReaderAdapter` that provides helpers for checkpoint cursors, capability tokens, and change detection.

### Rationale
- Keeps adapters deterministic and testable while centralizing hash computation + cursor persistence logic.
- Enables unit tests to stub adapters via the protocol instead of touching external APIs.
- Directly satisfies DOC-REQ-002/003 by ensuring each adapter emits `upsert`/`delete` events and advertises capability tokens matching queue derivation rules.

### Alternatives Considered
- Reusing the legacy `moonmind/manifest` loader classes: rejected because they lack change awareness, doc-hash tracking, and consistent metadata fields required for deterministic deletes.
- Building adapters inside the worker module: rejected to keep the engine testable independent of queue/runtime concerns.

## Transform + Chunking Strategy

### Decision
Use BeautifulSoup (`bs4`) for HTML stripping, then apply a deterministic `TokenTextSplitter` implemented via `tiktoken` (OpenAI tokenizer) with manifest-configurable `chunkSize`/`chunkOverlap`. Metadata enrichment (PathToTags, InferDocType) will be pure-Python utilities under `moonmind/manifest_v0/transforms/metadata.py` that operate on the manifest metadata/reader-provided context without external dependencies.

### Rationale
- BeautifulSoup + tiktoken are already transitive dependencies elsewhere in the repo, minimizing new packages.
- Deterministic tokenization ensures consistent chunk boundaries → stable point IDs.
- Metadata enrichers are deterministic + stateless, making them easy to test and ensuring DOC-REQ-004/007 compliance.

### Alternatives Considered
- Using `langchain` splitters: rejected to avoid a large dependency tree and to keep chunk IDs deterministic without hidden randomness.
- Writing a custom HTML parser: unnecessary effort vs. proven BeautifulSoup behavior.

## Embeddings + Vector Store Integration

### Decision
Create two factories: `embeddings_factory.py` that builds provider-specific clients (OpenAI, Google, Ollama) via provider adapters with consistent batching/progress hooks, and `vector_store_factory.py` that wraps the official Qdrant Python client. The vector factory enforces collection dimension/distance checks before upsert/delete calls and supports conditional creation when `allowCreateCollection=true`. Embedding batches emit counters (documents, chunks, elapsed time) for stage events without logging raw text.

### Rationale
- Aligns directly with DOC-REQ-005/006, letting manifests fully control embeddings provider/model plus vector store connection references.
- Centralizing checks prevents mismatched dimension errors at runtime and makes failure handling consistent.
- Factories keep Qdrant + embeddings dependencies isolated from the manifest worker so unit tests can mock them easily.

### Alternatives Considered
- Reusing existing vector store modules under `moonmind/vector_store`: rejected because they target generic RAG flows and do not enforce manifest-specific metadata/namespace policies.
- Allowing workers to instantiate SDKs ad hoc: rejected to keep runtime logic deterministic and to reuse progress instrumentation.

## Checkpoint Persistence Model

### Decision
Extend the existing `manifest` table with `state_json` and `state_updated_at` (already present from Phase 0) to store a JSON document keyed by `dataSource.id`. Each entry maps `source_doc_id` → `doc_hash`, includes the adapter-specific cursor (timestamp, file token, etc.), and tracks `last_run_started_at/finished_at`. The manifest worker reads the checkpoint before execution and writes back the updated JSON via a new `POST /api/manifests/{name}/state` endpoint invoked after successful runs; partial failures leave the previous checkpoint untouched.

### Rationale
- Avoids creating a brand-new table while satisfying DOC-REQ-009; JSON anchors line up with the doc’s suggested structure.
- Keeps checkpoint updates within the API service (manifest worker uses an authenticated callback) so DB writes stay centralized.
- JSON-based schema minimizes migrations while preserving room for per-adapter metadata.

### Alternatives Considered
- Separate `manifest_state` table: deferred until checkpoints become large enough to hurt registry operations; Phase 1 scope can operate comfortably in JSONB.
- Writing checkpoints directly from the worker via DB credentials: rejected to keep DB access in the API service for security/isolation.

## Worker Orchestration + Cancellation

### Decision
Implement `moonmind/agents/manifest_worker/worker.py` as a specialization of the codex worker loop: it polls `/api/queue/jobs?type=manifest`, claims jobs via the same lease API, and watches `cancelRequestedAt`. Stage execution is delegated to an `EngineRunner` helper that calls the manifest_v0 engine, emits stage events (`moonmind.manifest.<stage>`), and uploads artifacts after each stage. Cancellation requests raise `ManifestRunCancelled`, prompting the worker to finish the current batch, upload partial artifacts, emit `moonmind.manifest.finalize` with `status="cancelled"`, and acknowledge the cancel endpoint.

### Rationale
- Reuses proven queue/polling logic while isolating manifest-specific behavior into stage handlers.
- Keeps cancellation semantics aligned with existing workers, satisfying DOC-REQ-010/011/012.
- Allows artifact streaming + SSE events to piggyback on existing queue APIs instead of inventing new protocols.

### Alternatives Considered
- Running manifest ingestion via Celery tasks: rejected because the doc explicitly requires a dedicated manifest worker with queue lifecycle parity.
- Extending the codex worker to handle manifest jobs: rejected to avoid mixing build/exec runtimes with ingestion-specific dependencies and secrets.

## Secret Resolution & Artifact Redaction

### Decision
Share the vault/profile resolution helpers between codex and manifest workers by extracting common logic into `moonmind/agents/secret_refs/base.py`, then implementing manifest-specific wrappers that redact resolved secrets before writing `manifest/resolved.yaml`. The worker will strip any `${ENV_VAR}` placeholders that remain unresolved and fail validation before fetch/transform, ensuring no run proceeds without deploy-time credentials.

### Rationale
- Ensures DOC-REQ-012 compliance by preventing raw secrets from appearing in logs/artifacts.
- Reuse reduces maintenance overhead and keeps secret parsing uniform across workers.
- Validation-before-run avoids partial ingestion states caused by missing credentials.

### Alternatives Considered
- Injecting secrets via Celery config: rejected because the manifest worker runs as a standalone daemon, not within Celery tasks.
- Logging resolved credentials for debugging: explicitly forbidden by security guardrails; instead, artifacts will include reference strings only.
