# Implementation Plan: Manifest Task System Phase 0

**Branch**: `031-manifest-phase0` | **Date**: February 19, 2026 | **Spec**: `specs/031-manifest-phase0/spec.md`  
**Input**: Feature specification from `/specs/031-manifest-phase0/spec.md`

## Summary

Implement Agent Queue Phase 0 support for manifest ingestion runs by (1) registering `manifest` as a first-class job type with its own normalization contract, (2) persisting manifest registry records plus queue metadata, and (3) enforcing deterministic capability derivation, secret hygiene, and API/DB changes described in `docs/ManifestTaskSystem.md`. Research findings in `specs/031-manifest-phase0/research.md` confirm that we will centralize payload validation inside `moonmind/workflows/agent_queue/manifest_contract.py`, reuse it for both inline submissions and registry-backed runs, and surface sanitized queue metadata plus capability labels to keep manifest jobs isolated from codex/gemini traffic.

## Technical Context

**Language/Version**: Python 3.11 (api_service, moonmind.workflows, Celery worker code)  
**Primary Dependencies**: FastAPI, SQLAlchemy 2.x, Pydantic v2, Celery 5.4, RabbitMQ 3.x, PostgreSQL 15, PyYAML, Redis (dev cache), statsd instrumentation  
**Storage**: PostgreSQL (`agent_job*`, `manifest` tables) for queue + registry data; RabbitMQ for job dispatch; local filesystem (`var/artifacts/...`) for queue artifacts  
**Testing**: `./tools/test_unit.sh` (pytest runner) with suites under `tests/unit/**` plus schema fixtures  
**Target Platform**: Linux containers via `docker compose up rabbitmq celery-worker api`; workers share `/workspace` and `/var/run/docker.sock`  
**Project Type**: Multi-service backend (FastAPI API, Celery workers, shared workflow package)  
**Performance Goals**: `/api/manifests*` and `/api/queue/jobs?type=manifest` median latency <200 ms locally; queue job creation remains single INSERT + payload hash derivation; capability derivation must remain O(n data sources) to avoid blocking ingestion submissions  
**Constraints**: Queue payloads must be token-free (env/profile/vault references only), manifest actions restricted to `plan|run`, registry CRUD must hash every version, sanitized payloads required in API responses, and capability tags drive worker routing (manifest workers only).  
**Scale/Scope**: Phase 0 targets tens of concurrent manifest jobs, <100 registry entries, and compatibility with future manifest worker prototypes without forcing codex/gemini worker changes.

## Constitution Check

- `.specify/memory/constitution.md` is currently a template with unnamed principles, so no enforceable gates exist. Planning proceeds with the assumption that forthcoming constitution updates will retroactively document principles (e.g., “Test-First”, “Library-First”). Action item: once the constitution is ratified, rerun this gate to ensure manifest work complies with those principles; no blockers today because the document defines no requirements.

## Project Structure

```text
specs/031-manifest-phase0/
├── spec.md                # Requirements + DOC-REQ refs
├── research.md            # Phase 0 findings (contract, registry, secret policy)
├── data-model.md          # Entities (queue payload, manifest record, options)
├── quickstart.md          # Operator/dev workflow walkthrough
├── contracts/
│   ├── manifest-task-system-phase0.openapi.yaml
│   └── requirements-traceability.md
└── plan.md                # ← this document

moonmind/workflows/agent_queue/
├── job_types.py           # Canonical job type constants incl. MANIFEST
├── manifest_contract.py   # Validation, normalization, capability derivation
├── service.py             # AgentQueueService routing + manifest handling
├── repositories.py        # DB persistence, claim filters, capability enforcement
└── models.py/storage.py   # SQLAlchemy models + artifact storage utilities

api_service/
├── api/routers/
│   ├── agent_queue.py     # Queue CRUD endpoints w/ sanitized manifest payloads
│   └── manifests.py       # Registry CRUD + /runs bridge into queue service
├── api/schemas.py         # Pydantic models for manifests + queue responses
├── services/manifests_service.py  # Registry orchestration + queue job submission
├── db/models.py           # `ManifestRecord` definition (hashes, checkpoint cols)
└── migrations/versions/
    ├── cb32e6509d1a_add_manifest_table.py
    ├── 202602190003_manifest_registry_extensions.py
    └── 202602190004_manifest_hash_length.py

tests/
├── unit/workflows/agent_queue/test_manifest_contract.py
├── unit/workflows/agent_queue/test_service_manifest.py
├── unit/api/routers/test_manifests.py
└── unit/api/services/test_manifests_service.py
```

**Structure Decision**: Maintain the existing shared `moonmind.workflows` package for queue logic and the FastAPI `api_service` for HTTP surfaces. Manifest-specific behavior belongs alongside existing queue modules to keep worker + API code paths coherent, while registry CRUD lives in `api_service` so migrations/models stay near the DB layer.

## Implementation Plan

### 1. Queue Job Type Registration & Worker Routing
- Ensure `moonmind/workflows/agent_queue/job_types.py` exports `MANIFEST_JOB_TYPE` and `SUPPORTED_QUEUE_JOB_TYPES`, and import these constants in `AgentQueueService`, repositories, and worker auth policy enforcement.
- Extend `AgentQueueService.create_job()` so manifest submissions exclusively hit `normalize_manifest_job_payload()`; legacy task types continue using `task_contract.py`.
- Update `AgentQueueRepository._is_job_claim_eligible` to require workers to advertise `requiredCapabilities` ⊇ job payload capabilities, preventing codex/gemini workers from touching manifest jobs.
- Surface sanitized payload metadata (type, manifest hash/version, required capabilities) via `moonmind/schemas/agent_queue_models.JobModel` so `/api/queue/jobs?type=manifest` lists remain token-free (FR-012, DOC-REQ-001).

### 2. Manifest Payload Contract & Secret Enforcement
- Keep `normalize_manifest_job_payload()` as the single entry point for manifest validation:
  - Enforce `manifest.name == metadata.name`, limit `manifest.source.kind` to `inline|registry` (guarded `path` enabled behind `settings.spec_workflow.allow_manifest_path_source`), and fail fast on unsupported adapters/actions (FR-002/FR-014/FR-015).
  - Restrict queue overrides to `dryRun|forceFull|maxDocs`; merge them into `effectiveRunConfig` to maintain deterministic runtime config snapshots.
  - Compute `manifestHash` (`sha256:<digest>`) and `manifestVersion` (Phase 0 accepts only `"v0"`), then derive `requiredCapabilities` by walking embeddings/vectorStore/dataSources (FR-005, User Story 3).
- Run `detect_manifest_secret_leaks()` against manifest YAML + overrides to block raw secrets, only allowing `${ENV}`, `profile://`, or `vault://` references (FR-007/DOC-REQ-004/008).
- Emit `manifestSecretRefs` metadata (profile + vault lists) to help the forthcoming manifest worker fetch credentials without YAML scraping (FR-016).

### 3. Manifest Registry CRUD & Queue Bridge
- Expand `api_service/db/models.ManifestRecord` and Alembic migrations to keep `content`, `content_hash`, `version`, audit timestamps, and checkpoint placeholders required by FR-011.
- Implement `api_service/services/manifests_service.py` to:
  - Normalize every PUT payload via the manifest contract before persisting (guarantees identical validation for inline vs. registry flows).
  - Expose typed helpers for `list`, `get`, `upsert`, and `submit_manifest_run`, linking queue job IDs/status back to registry rows (`last_run_*` fields).
- Add `/api/manifests`, `/api/manifests/{name}`, and `/api/manifests/{name}/runs` routers with FastAPI dependencies (`ManifestsService`, authenticated user) and error handling that translates `ManifestContractError` + `AgentQueueValidationError` into HTTP 422 responses (FR-008–FR-010).
- Ensure `POST /api/manifests/{name}/runs` loads registry content, injects a `source.kind="registry"` payload (without exposing inline YAML), and returns the sanitized queue metadata defined in `api_service/api/schemas.py`.

### 4. Schema & Event Propagation
- Update `moonmind/workflows/agent_queue/models.AgentJob` payload storage (JSONB) to include manifest metadata, ensuring events and SSE feeds propagate `manifestSecretRefs`, `requiredCapabilities`, and sanitized `manifest` shape.
- Confirm `moonmind/schemas/agent_queue_models` plus SSE/event serializers call `sanitize_manifest_payload()` so dashboards and CLI consumers only see hash/version/capability data.
- Extend queue/event listing filters so `type=manifest` can be queried independently, meeting DOC-REQ-001 and enabling UI efforts without backend rewrites.

### 5. Testing & Validation Strategy
- Extend/author unit coverage (run via `./tools/test_unit.sh`) across:
  - Contract tests: happy path normalization, capability derivation for supported adapters, failure cases (missing embeddings/vectorStore/dataSources, secret heuristics, unsupported actions/options, name mismatches, disallowed source kinds).
  - Registry router/service tests: upsert workflow, search/list filtering, `/runs` bridging into queue, validation errors for secret leaks + unknown manifests.
  - Queue service/repository tests: manifest job creation, capability gating, sanitized payload output, worker claim restrictions.
- Add quickstart recipes (already staged in `quickstart.md`) to validate operator workflows manually if needed; keep integration tests optional per instructions (unit tests remain CI gate).

### 6. Risks & Mitigations
- **Schema Drift**: `manifest_models.py` updates could invalidate contract assumptions. Mitigate by reusing schema loader helpers or adding regression tests keyed to sample manifests from `examples/*.yaml`.
- **Secret False Positives**: Aggressive heuristics might reject valid manifests. Provide targeted unit tests plus allowlist docs for `${ENV}/profile/vault` refs and keep error messages actionable.
- **Worker Capability Sync**: If manifest workers fail to advertise derived capabilities, claims will starve. Document capability requirements for worker deploy scripts and add monitoring on queue claim failures filtered by capability mismatch.
- **Constitution Placeholder**: Current constitution lacks spelled-out principles. Track a follow-up to populate it so future planning gates can confirm compliance (no action within Phase 0).

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| _None_ | Manifest Phase 0 stays within existing architecture boundaries | Constitution currently defines no constraints to waive |
