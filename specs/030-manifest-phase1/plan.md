# Implementation Plan: Manifest Task System Phase 1

**Branch**: `[030-manifest-phase1]` | **Date**: February 19, 2026 | **Spec**: `specs/030-manifest-phase1/spec.md`
**Input**: Feature specification from `/specs/030-manifest-phase1/spec.md`

## Summary

Phase 1 implements the execution half of the manifest task system so queued manifests actually ingest content declaratively. Delivery spans the new `moonmind/manifest_v0` package (models, adapters, transforms, embeddings/vector store factories, id policy, checkpoint/state handling) and the `moonmind-manifest-worker` runtime that claims `type="manifest"` jobs, emits stage events, uploads artifacts, honors cancellations, and redacts secrets. The engine must support inline + registry source kinds, deterministically chunk/embed/upsert/delete documents into Qdrant using manifest-provided embeddings, and persist checkpoint state through the manifest registry so incremental reruns skip unchanged data. Unit/integration tests executed via `./tools/test_unit.sh` will target adapters, factories, state persistence, worker orchestration, cancellation, and artifact/event publication to satisfy the runtime guard.

## Technical Context

**Language/Version**: Python 3.11 (matching MoonMind services + Celery workers)  
**Primary Dependencies**: FastAPI, SQLAlchemy, Celery, httpx, PyYAML, Pydantic v2, Qdrant client SDK, selected embedding SDKs (OpenAI, Google Generative AI, Ollama), `tiktoken`/`nltk`-style tokenizers for chunking  
**Storage**: PostgreSQL `manifest` table (plus new `state_json` fields) for registry + checkpoints, Qdrant vector store for manifests, object storage/artifact bucket for worker reports  
**Testing**: `./tools/test_unit.sh` (pytest wrapper) for manifests engine + worker, with new unit suites under `tests/unit/manifest_v0`, `tests/unit/agents/manifest_worker`, and contract tests for registry checkpoint updates  
**Target Platform**: MoonMind API + Celery workers running in Docker/WSL; manifest worker is a Python CLI/daemon deployed as its own service container  
**Project Type**: Backend services + worker runtime + supporting libraries  
**Performance Goals**: Manifest runs must stream stage events within <2 s of stage transitions, embed batches sized to keep Qdrant upsert throughput ≥2k chunks/minute on reference manifests, and incremental reruns should avoid >10% redundant embeddings when checkpoints indicate no changes  
**Constraints**: No raw secrets in payloads/events/artifacts; deterministic point IDs (sha256 of manifest/dataSource/doc/chunk/embedding info); engine limited to `vectorStore.type="qdrant"`; enforcement of `manifest.options` override rules; runtime guard demands production code + automated tests  
**Scale/Scope**: Expected dozens of manifests covering GitHub/Drive/Confluence/local FS with tens of thousands of documents per run, requiring resumable processing and cancellation-safe batching

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` remains an unratified template with placeholder headings, so there are no enforced project-specific principles beyond repository guardrails.
- Default governance therefore defers to AGENTS security/runtime guardrails (no secrets, runtime deliverables + tests).

**Gate Status**: PASS WITH NOTE — document absence of a ratified constitution and proceed.

## Project Structure

### Documentation (this feature)

```text
specs/030-manifest-phase1/
├── plan.md                       # This file (speckit-plan output)
├── research.md                   # Phase 0 findings for engine + worker
├── data-model.md                 # ManifestV0 entities, SourceChange, checkpoints
├── quickstart.md                 # Runbook for manifest worker + sample run
├── contracts/
│   ├── manifest-phase1.openapi.yaml     # Stage event + artifact schema contract
│   └── requirements-traceability.md     # DOC-REQ ↔ FR mapping
├── checklists/requirements.md    # From speckit-specify
└── tasks.md                      # Generated later via speckit-tasks
```

### Source Code (repository root)

```text
moonmind/
├── manifest_v0/
│   ├── __init__.py
│   ├── models.py                 # ManifestV0 schema + run config parsing
│   ├── yaml_io.py                # Load/serialize helpers with redaction
│   ├── validator.py              # Schema + semantic validation
│   ├── interpolate.py            # Env/profile/vault resolution wrappers
│   ├── secret_refs.py            # Vault/profile reference parsing + redaction
│   ├── readers/
│   │   ├── base.py               # ReaderAdapter protocol + dataclasses
│   │   ├── github.py
│   │   ├── google_drive.py
│   │   ├── confluence.py
│   │   └── simple_directory.py
│   ├── transforms/
│   │   ├── html.py
│   │   ├── splitter.py
│   │   └── metadata.py
│   ├── embeddings_factory.py
│   ├── vector_store_factory.py
│   ├── id_policy.py
│   ├── state_store.py
│   ├── engine.py                 # plan()/run() orchestration + checkpoints
│   └── reports.py
├── agents/
│   ├── codex_worker/...          # Existing reference implementation
│   └── manifest_worker/
│       ├── __init__.py
│       ├── cli.py                # `poetry run moonmind-manifest-worker`
│       ├── worker.py             # Queue loop, preflight, cancellation
│       ├── handlers.py           # Stage execution + artifact upload helpers
│       └── secret_refs.py        # Shared vault/profile resolution utilities
└── workflows/agent_queue/
    ├── manifest_contract.py      # Already created in Phase 0 (reuse for payloads)
    ├── job_types.py/service.py   # Provide manifest job type + queue orchestration hooks
    └── repositories.py           # Persist checkpoint state + events from worker

api_service/
├── api/routers/manifests.py      # Extend to expose checkpoint info + worker artifacts metadata
├── services/manifests_service.py # Persist `state_json`, `last_run_*` after worker callbacks
└── schemas/agent_queue_models.py # SSE/event schema additions for manifest stages

moonmind/workflows/agent_queue/manifest_contract.py already exists; Phase 1 will reuse derivatives for worker-run contexts rather than modifying queue submission rules extensively.

tests/
├── unit/manifest_v0/             # Engine, adapters, factories, checkpoint tests
├── unit/agents/test_manifest_worker.py
├── unit/api/routers/test_manifests_checkpoint.py
└── unit/workflows/agent_queue/test_manifest_integration.py
```

**Structure Decision**: Mirror the existing `moonmind/manifest` legacy runner by introducing a new `manifest_v0` package dedicated to the declarative pipeline, keeping adapters/transforms modular for future reuse. The manifest worker lives beside the codex worker inside `moonmind/agents/` to share queue loop utilities while remaining specialized (`allowed_types=["manifest"]`). API/service layers only require incremental updates (manifest registry state writes, SSE schemas) rather than large restructures.

## Phase 0: Research Plan

1. **Adapter + Reader Contracts** — Validate how existing loaders (`moonmind/manifest/*`) fetch GitHub/Drive/Confluence/local FS data and decide what must change to emit `SourceDocument`/`SourceChange` objects with hashes + metadata. Research required SDKs/ratelimits for each adapter and confirm available test fixtures.
2. **Transform + Chunking Strategy** — Evaluate open-source HTML stripping/tokenization utilities (BeautifulSoup, `bs4`, `tiktoken`, `langchain` chunkers) to choose deterministic, dependency-light implementations that match the doc’s requirements.
3. **Embeddings + Vector Store Integration** — Determine how to wrap OpenAI/Gemini/Ollama SDKs to enforce dimension checks before Qdrant upserts and to stream progress events without leaking content; confirm Qdrant Python client usage for delete-by-filter semantics.
4. **Checkpoint Persistence Model** — Design the structure for `ManifestRecord.state_json` (per dataSource doc-hash map, cursors, timestamps) or decide if a new `manifest_state` table is warranted; confirm transactional semantics when worker writes back state after success.
5. **Worker Orchestration + Cancellation** — Study the codex worker’s queue loop to understand heartbeat/cancel handling, artifact uploads, SSE event posting, and decide what can be reused vs. rewritten for manifest-specific stages and metrics.
6. **Security & Secret Resolution** — Evaluate reuse of `moonmind/agests/codex_worker/secret_refs.py` vs. building manifest-specific helpers to ensure env/profile/vault references resolve without exposing raw secrets, especially when generating `manifest/resolved.yaml` artifacts.

## Phase 1: Design Outputs

- `research.md` — Document outcomes/decisions for adapters, transforms, embeddings/vector store integration, checkpoint schema, worker orchestration, and secret handling.
- `data-model.md` — Detail ManifestV0 schema, SourceDocument/SourceChange dataclasses, CheckpointState layout, StageEvent payloads, and artifact metadata.
- `contracts/manifest-phase1.openapi.yaml` — Define REST + SSE schema slices for manifest worker events (`moonmind.manifest.*`), artifact listings, and checkpoint update callbacks.
- `contracts/requirements-traceability.md` — Map DOC-REQ-001…013 to FR-001…FR-013 with implementation surfaces + validation plans.
- `quickstart.md` — Provide a runnable guide covering manifest worker bootstrap, submitting a manifest, monitoring events/artifacts, forcing incremental reruns, and cancellation tests.

## Post-Design Constitution Re-check

- No constitution updates were introduced; governance remains placeholder-only.
- The plan continues to honor security + runtime guardrails (no secrets, automated tests required).

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
