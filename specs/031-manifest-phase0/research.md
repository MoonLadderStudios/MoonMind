# Phase 0 Research: Manifest Task System

**Feature**: Manifest Task System Phase 0  
**Branch**: `031-manifest-phase0`  
**Date**: February 19, 2026

## Manifest Payload Contract

- **Decision**: Keep `moonmind/workflows/agent_queue/manifest_contract.py::normalize_manifest_job_payload` as the single entry point for manifest queue submissions. The helper parses YAML with PyYAML, enforces `manifest.name == metadata.name`, limits `manifest.options` to `dryRun | forceFull | maxDocs`, computes `manifestHash` (`sha256:<digest>`), sets `manifestVersion` (Phase 0 accepts only `"v0"` per docs §6.2), derives `requiredCapabilities`, and merges queue overrides into the manifest `run` block to produce `effectiveRunConfig`.
- **Rationale**: Centralizing normalization isolates manifest semantics from `task_contract.py`, guarantees inline and registry submissions share identical invariants, and lets registry flows reuse the exact same validation logic. Persisting normalized payloads fulfills FR-003/004/005/006 without duplicating code paths.
- **Alternatives Considered**: Reusing `task_contract.py` would drag task-specific vault auth rules and optional publish/repo overrides into manifest runs, violating DOC-REQ-002. Letting clients provide pre-derived capability lists would undermine DOC-REQ-003 because workers could be tricked into claiming incompatible jobs.

## Capability Derivation & Worker Enforcement

- **Decision**: `derive_required_capabilities()` always emits `manifest`, `embeddings`, provider label (`openai`, `google`, `ollama`), vector store capability (`qdrant`, `pgvector`, `milvus`), and one capability per `dataSources[].type` (`GithubRepositoryReader → github`, `GoogleDriveReader → gdrive`, `ConfluenceReader → confluence`, `SimpleDirectoryReader → local_fs`). `AgentQueueRepository._is_job_claim_eligible` already requires workers to advertise a superset of `requiredCapabilities`, so manifest workers must register at least `manifest` plus all derived tokens in their worker tokens.
- **Rationale**: Server-side derivation gives deterministic worker routing, ensures `/api/queue/jobs?type=manifest` can display meaningful metadata, and satisfies DOC-REQ-001 + User Story 3 (capability-based routing). Capability chips also act as audit breadcrumbs during incident response.
- **Alternatives Considered**: Encoding capabilities inside worker IDs or custom job affinity keys would complicate filtering and make queue listings less expressive. Allowing a “wildcard manifest” capability would prevent selective scheduling (e.g., qdrant vs pgvector workers) and contradict §6.5.

## Registry Model & Hashing

- **Decision**: Reuse the manifest contract during registry operations: `api_service/services/manifests_service.py::upsert_manifest` calls normalization (forcing `action="plan"` / `source.kind="inline"`) to compute `content_hash` + `version`, then persists YAML + hashes in `api_service/db/models.ManifestRecord`. The Alembic migration `202602190003_manifest_registry_extensions.py` adds `version`, `created_at`, `updated_at`, `last_run_*`, and `state_json/state_updated_at`, satisfying FR-011. Registry-backed runs inject stored YAML into a `source.kind="registry"` payload (with inline content stripped before persistence) and capture the queue job id/status/timestamps on the manifest record.
- **Rationale**: Running every registry write through the same normalization logic eliminates drift between inline and registry submissions, keeps `content_hash` deterministic (SC-003), and records enough run metadata for future dashboards. Persisting checkpoint-friendly columns now avoids breaking migrations when Phase 1 introduces incremental sync state.
- **Alternatives Considered**: Skipping normalization during registry PUTs would allow invalid YAML or mismatched names to land in Postgres, only to fail later during queue submission. Maintaining separate “manifest versions” tables was deemed unnecessary for Phase 0 because governance only needs the latest definition plus audit timestamps.

## Secret Policy & Raw Token Detection

- **Decision**: Add `detect_manifest_secret_leaks()` to `manifest_contract.py` and run it during normalization. The helper recursively inspects manifest YAML + queue override strings and raises `ManifestContractError` when encountering likely raw secrets (patterns such as `sk-`, `ghp_`, `AIza`, `AKIA`, `token=`, `secret=`, 40+ char base64-like blobs, JWT-looking strings). Strings that begin with `${`, `profile://`, or `vault://` are treated as sanctioned references per docs §11.1. Violations block job creation before persistence (FR-007, DOC-REQ-004/005).
- **Rationale**: Queue payloads and registry rows must remain token-free; rejecting suspicious literals at validation time is cheaper than attempting redaction later and ensures manifests can be safely echoed in queue listings/artifacts. The heuristic keeps false positives low by allowing env/profile/vault references while aggressively blocking obvious credential patterns.
- **Alternatives Considered**: Post-processing payloads to redact after persistence would still store secrets in Postgres and risk worker leakage. Limiting checks to specific manifest fields (e.g., `vectorStore.connection`) was rejected because manifests may evolve; a recursive scanner keeps enforcement generic.

## Queue Serialization & Sanitization

- **Decision**: `moonmind/schemas/agent_queue_models.JobModel` (plus SSE/event serializers) run `sanitize_manifest_payload()` when `job.type == MANIFEST_JOB_TYPE`, ensuring API responses include only `manifest.name`, `action`, `source.kind/name`, `manifestHash`, `manifestVersion`, `requiredCapabilities`, and `effectiveRunConfig`. Inline YAML content is never returned to clients, yet workers still receive the full payload from the repository.
- **Rationale**: Sanitizing at serialization time preserves manifest privacy without introducing additional DB columns or dual-payload storage. It also meets FR-012 by letting dashboards present manifest jobs as a distinct category with safe metadata.
- **Alternatives Considered**: Storing two payload versions (raw + sanitized) would complicate migrations and risk divergence. Relying entirely on UI filtering without API-level sanitization would violate DOC-REQ-004 because API clients could still fetch inline YAML.

## Validation & Test Strategy

- **Decision**: Extend pytest coverage (run via `./tools/test_unit.sh`) across the following suites:  
  - `tests/unit/workflows/agent_queue/test_manifest_contract.py` (happy paths, invalid adapters/options, registry submissions, secret detection).  
  - `tests/unit/api/routers/test_manifests.py` + `tests/unit/services/test_manifests_service.py` (registry CRUD, run submission, secret rejection).  
  - `tests/unit/api/routers/test_agent_queue.py` + `tests/unit/workflows/agent_queue/test_repositories.py` (queue filtering, capability gating, sanitized payload assertions).  
  - Any new helper tests for secret heuristics if logic becomes non-trivial.  
  This suite gives CI-level proof for FR-013 and ensures regression coverage before manifest workers exist.
- **Alternatives Considered**: Deferring to Docker Compose integration tests would slow iteration and violate the runtime intent guard. Lint-only or doc-only validation was rejected because spec explicitly demands production code plus automated tests.
