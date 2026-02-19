# Feature Specification: Manifest Task System Phase 1

**Feature Branch**: `[030-manifest-phase1]`  
**Created**: February 19, 2026  
**Status**: Draft  
**Input**: User description: "Implement Phase 1 of the manifest task system as described in docs/ManifestTaskSystem.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

- **DOC-REQ-001 (ManifestTaskSystem §8.1-§8.2)**: Build the dedicated `moonmind/manifest_v0` package with models, YAML IO, validation, interpolation, secret handling, adapters, transforms, embeddings factory, vector store factory, id policy, state store, engine orchestration, and reports that drive validate → fetch → transform → embed → upsert/delete pipelines.
- **DOC-REQ-002 (ManifestTaskSystem §8.3)**: Implement the `ReaderAdapter` protocol, including `SourceDocument`, `SourceChange`, and `PlanStats` data contracts so adapters can plan, emit `upsert`/`delete` changes, and checkpoint incremental state.
- **DOC-REQ-003 (ManifestTaskSystem §8.4)**: Ship the baseline adapters (`GithubRepositoryReaderAdapter`, `GoogleDriveReaderAdapter`, `ConfluenceReaderAdapter`, `SimpleDirectoryReaderAdapter`) and map each to the queue capability tokens described in the doc.
- **DOC-REQ-004 (ManifestTaskSystem §8.5-§8.6)**: Provide deterministic transform + chunking stages (HTML stripping, splitter, metadata enrichment) that output chunk hashes/doc hashes so unchanged content can be skipped and deletions detected.
- **DOC-REQ-005 (ManifestTaskSystem §8.7)**: Create an embeddings factory that honors the manifest `embeddings` block, enforces dimension compatibility with the destination collection, and batches work while emitting safe progress events.
- **DOC-REQ-006 (ManifestTaskSystem §8.8)**: Implement a vector store factory for `vectorStore.type="qdrant"` that can connect via env/profile/vault references, optionally create collections when allowed, and fail fast when collections or distance metrics mismatch expectations.
- **DOC-REQ-007 (ManifestTaskSystem §8.9)**: Every upserted point must include manifest name, data source id, doc id, doc hash, chunk index, and only allowlisted metadata fields to enforce namespace isolation and leakage prevention.
- **DOC-REQ-008 (ManifestTaskSystem §8.10)**: Enforce deterministic point IDs, delete-then-upsert semantics for changed documents, delete-by-filter when documents disappear, and respect `forceFull` overrides for complete re-syncs.
- **DOC-REQ-009 (ManifestTaskSystem §8.11 & §7.1)**: Persist checkpoint state per `(manifest.name, dataSource.id)` inside the manifest registry (`state_json` or companion table) with timestamps so runs resume incrementally after success.
- **DOC-REQ-010 (ManifestTaskSystem §9.1-§9.2)**: Introduce the `moonmind-manifest-worker` service that only claims `type="manifest"` jobs, validates environment/config (Qdrant reachability, embedding credentials) before processing, and advertises capabilities that cover manifest pipelines.
- **DOC-REQ-011 (ManifestTaskSystem §9.3-§9.4)**: The worker must emit ordered stage events (`validate`→`plan`→`fetch`→`transform`→`embed`→`upsert`→`finalize`) with counts/timings and upload required artifacts (logs, original/resolved manifests, plan/run summaries, checkpoints, errors) with secret redaction.
- **DOC-REQ-012 (ManifestTaskSystem §9.5 & §11.1-§11.3)**: Support cancellation requests, finish safe boundaries, acknowledge cancellation, and ensure logs/artifacts never leak raw secrets—only env/profile/vault references.
- **DOC-REQ-013 (ManifestTaskSystem §6.3-§6.4 & Phase 1 scope)**: Manifest ingestion must accept `inline` and `registry` source kinds (with `path` acceptable for dev/test), respect action modes (`plan` vs `run`), and honor queue-level overrides for `dryRun`, `forceFull`, and `maxDocs` without allowing structural overrides.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Execute Declarative Manifest Runs End-to-End (Priority: P1)

A manifest operator queues a `manifest` job referencing a v0 YAML definition and expects the manifest worker to validate, fetch sources, transform/chunk, embed, upsert/delete in Qdrant, generate artifacts, and update checkpoints without manual intervention.

**Why this priority**: Phase 1’s purpose is to deliver the actual ingestion pipeline; without end-to-end execution the earlier queue plumbing provides no business value.

**Independent Test**: Submit a manifest run via `/api/queue/jobs` or `/api/manifests/{name}/runs`, let the manifest worker process it, and verify Qdrant receives deterministic points, checkpoints persist, and artifacts summarize the run.

**Acceptance Scenarios**:

1. **Given** a valid manifest referencing GitHub + Confluence sources, **When** the manifest worker claims the job, **Then** it emits each stage event, uploads plan/run summary artifacts, writes/upserts points with stable IDs, and persists checkpoint state.
2. **Given** a manifest whose manifest YAML metadata name differs from the payload, **When** the worker validates during `moonmind.manifest.validate`, **Then** it fails fast, produces an error artifact, and leaves Qdrant unchanged.

---

### User Story 2 - Incremental Sync with Checkpoints (Priority: P2)

A platform engineer reruns a manifest after documents were updated in the source systems and expects the engine to skip unchanged documents, upsert only modified chunks, and delete entries for removed docs using stored checkpoints.

**Why this priority**: Incremental and idempotent behavior avoids re-embedding entire corpora and makes manifests reliable for production syncs.

**Independent Test**: Run the same manifest twice, modify a single document between runs, and confirm only the affected points are re-embedded while deletions happen when documents disappear or `forceFull` is toggled.

**Acceptance Scenarios**:

1. **Given** a prior successful run stored checkpoint hashes, **When** the next run sees identical doc hashes, **Then** the engine skips embedding/upsert work, emits plan stats that show zero changes, and finishes faster.
2. **Given** a manifest run executed with `forceFull=true`, **When** documents are missing compared to the previous checkpoint, **Then** the engine deletes the stale points and records deletion counts in the run summary artifact.

---

### User Story 3 - Observe and Govern Manifest Runs (Priority: P3)

A queue administrator monitors manifest jobs via the existing task dashboard/SSE feed and expects manifest runs to publish detailed stage events, artifact links, and cancellation acknowledgements distinct from codex/gemini jobs.

**Why this priority**: Operators must see whether manifests are progressing, stalled, or cancelled and need artifacts for audits/debugging.

**Independent Test**: Watch SSE events for a manifest job, cancel the job mid-run, and confirm events include stage transitions, counts, artifact uploads, and a cancellation acknowledgement before the worker stops.

**Acceptance Scenarios**:

1. **Given** a manifest job is mid-`embed`, **When** an admin issues a cancellation via the queue API, **Then** the worker completes the in-flight batch, emits a cancellation event, uploads partial summaries, and acknowledges the cancel request.
2. **Given** manifest jobs run daily, **When** the admin reviews artifacts, **Then** each run includes `manifest/resolved.yaml` with references redacted plus run summaries listing documents processed, embeddings counts, and deletion totals.

---

### Edge Cases

- Source adapter returns an unexpected capability token; the engine must reject the manifest before execution so jobs never become unclaimable.
- Qdrant collection dimension mismatches the manifest embeddings configuration; worker preflight must detect and fail with actionable guidance rather than producing corrupt points.
- Manifest references a secret via `${ENV_VAR}` that is missing at runtime; worker should halt during validation, emit an error artifact, and never log raw secret placeholders.
- Registry-backed run references a manifest whose YAML has changed since the job was queued; manifest hash/version stored in payload must let the worker detect drift and optionally warn/abort according to doc guidance.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (DOC-REQ-001)**: Create the `moonmind/manifest_v0` package tree (models, yaml_io, validator, interpolate, secret_refs, readers, transforms, embeddings_factory, vector_store_factory, id_policy, state_store, engine, reports) and wire it into the runtime so manifest jobs can call `engine.plan`/`engine.run` entry points.
- **FR-002 (DOC-REQ-002)**: Define `SourceDocument`, `SourceChange`, and `PlanStats` dataclasses plus the `ReaderAdapter` protocol, then refactor all adapters to emit deterministic `upsert` or `delete` changes along with checkpoint snapshots.
- **FR-003 (DOC-REQ-003)**: Implement the four baseline adapters (GitHub repository, Google Drive, Confluence, Simple Directory) with capability labels that match queue derivation rules, ensuring each adapter knows how to load credentials via env/profile/vault references only.
- **FR-004 (DOC-REQ-004)**: Build deterministic transform pipelines that include HTML stripping, token-based chunking, and metadata enrichment, producing chunk + doc hashes so idempotency logic can skip unchanged data and support deletions.
- **FR-005 (DOC-REQ-005)**: Develop an embeddings factory that chooses providers/models from the manifest, enforces dimension compatibility with the Qdrant collection, and emits safe progress events for batching and timings.
- **FR-006 (DOC-REQ-006 & DOC-REQ-013)**: Provide a vector store factory for Qdrant that resolves connections from env/profile/vault refs, optionally creates collections when `allowCreateCollection=true`, validates distance metric + dimensions, and supports inline/registry/path source kinds with respect to action (`plan` vs `run`) and queue overrides (`dryRun`, `forceFull`, `maxDocs`).
- **FR-007 (DOC-REQ-007)**: Apply metadata allowlists at ingestion time so every point includes manifest + dataSource context, doc identifiers, doc/chunk hashes, and only explicitly allowed metadata fields before upserting into Qdrant.
- **FR-008 (DOC-REQ-008)**: Implement stable SHA-256 point IDs derived from manifest name, data source, source doc id, chunk index, and embeddings context; delete old points before re-upserting changed docs and delete-by-filter for removed docs.
- **FR-009 (DOC-REQ-009)**: Store checkpoint state in the manifest registry (`state_json` or sub-table), update it after successful runs, include timestamps, and reload it at run start to drive incremental fetch/delete logic.
- **FR-010 (DOC-REQ-010)**: Introduce the `moonmind-manifest-worker` executable/service that claims `type="manifest"` jobs, performs preflight checks (Qdrant reachability, embeddings credentials, collection readiness), and uses queue heartbeats consistent with existing worker patterns.
- **FR-011 (DOC-REQ-011)**: Emit ordered stage events with counts/timings to the queue event stream and upload required artifacts (logs, input/resolved manifests, plan.json, run_summary.json, checkpoint.json, errors.json) with token redaction to keep operators informed.
- **FR-012 (DOC-REQ-012)**: Honor cancellation requests by exiting at safe boundaries, acknowledging the cancel event, and ensuring logs/artifacts redact secrets and reflect partial completion where applicable.
- **FR-013 (Runtime intent guard)**: Deliver production runtime code plus automated unit/integration coverage (executed via `./tools/test_unit.sh`) for manifest_v0 packages, adapters, vector + embedding factories, worker orchestration, and cancellation paths so Phase 1 ships with verifiable runtime behavior.

### Key Entities *(include if feature involves data)*

- **ManifestV0**: Typed representation of the manifest YAML (metadata, dataSources, transforms, embeddings, vector store, security, run config) consumed by the engine and adapters.
- **SourceDocument / SourceChange**: Data plane structures emitted by adapters to describe fetched content, document hashes, metadata, and whether the change is an upsert or delete.
- **CheckpointState**: Serialized per-manifest/dataSource state (timestamps, doc-hash maps, cursors) stored in the registry so incremental runs resume accurately.
- **ManifestWorkerRunContext**: Aggregates queue payload, derived capabilities, resolved secret references, Qdrant connection, and stage progress for a single job claim.
- **StageEventPayload**: Structured data sent during `moonmind.manifest.*` events containing stage names, counts, durations, manifest identifiers, and artifact references for observability.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For representative manifests covering all four adapters, 95% of re-runs finish with ≤10% of the embedding workload of the initial run thanks to checkpointed hash comparisons, as verified by automated tests.
- **SC-002**: Manifest worker emits every required stage event and artifact for at least one happy-path and one failure-path test run, with SSE logs confirming ordered transitions and no missing stages.
- **SC-003**: Cancellation tests demonstrate the worker acknowledges cancel requests within 10 seconds, stops further Qdrant writes, and uploads partial run summaries describing processed/deleted document counts.
- **SC-004**: `./tools/test_unit.sh` executes a suite that exercises adapters, embedding/vector factories, checkpoint persistence, worker preflight, and cancellation flows with ≥90% code coverage for the new manifest_v0 + worker modules.

## Assumptions & Constraints

- Phase 1 builds on Phase 0 queue plumbing and registry APIs, so job type registration and manifest normalization already exist and do not need duplication.
- Qdrant remains the only supported vector store in this phase; additional stores will be considered in later phases once the v0 engine proves stable.
- Secrets are always referenced via env/profile/vault tokens; neither queue payloads nor artifacts may contain raw credentials, and workers must fail fast when references cannot be resolved.
- Runtime code must be validated by running `./tools/test_unit.sh`; no docs-only deliverables satisfy the runtime guard for this feature.

## Dependencies

- Existing Agent Queue infrastructure for job submission, heartbeats, cancellation, and artifact upload APIs.
- Manifest registry schema (`manifest` table + new `state_json` columns) introduced in Phase 0 to persist manifest definitions and checkpoint state.
- Qdrant deployments accessible from worker environments plus embedding provider credentials (OpenAI, Google, Ollama) configured via env/profile/vault references.
