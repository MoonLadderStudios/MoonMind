# Implementation Plan: Manifest Task System Phase 1 (Worker Readiness)

**Branch**: `030-manifest-phase1` | **Date**: 2026-03-02 | **Spec**: `specs/030-manifest-phase1/spec.md`  
**Input**: Feature specification from `/specs/030-manifest-phase1/spec.md`

## Summary

Phase 0 control-plane foundations for manifest queue jobs are already in the repository.  
Phase 1 is narrowed to worker-readiness surfaces: worker-only secret resolution for running claimed manifest jobs, manifest state callback persistence, and validation coverage.  
Selected orchestration mode is runtime (not docs-only), so completion requires production runtime code paths plus `./tools/test_unit.sh` validation.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async service/repository stack, AuthProviderManager (`profile` + env fallback)  
**Storage**: PostgreSQL manifest registry (`manifest` table with `state_json`, `state_updated_at`, `last_run_*` fields) and existing agent queue tables  
**Testing**: `./tools/test_unit.sh`  
**Target Platform**: MoonMind API service and worker-token authenticated queue clients in Docker/Linux deployment  
**Project Type**: Backend API + service-layer runtime feature  
**Performance Goals**: Preserve existing queue/manifest API latency characteristics; add no new long-running operations to request path  
**Constraints**: Queue payloads remain token-free; secret resolution restricted to manifest-capable owning worker on running jobs; fail fast on unresolved profile refs; keep runtime-vs-docs behavior aligned with runtime mode  
**Scale/Scope**: Control-plane worker readiness only; excludes `manifest_v0` execution engine and dedicated manifest daemon rollout

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Deployment with Smart Defaults**: PASS. No new required services or secrets; additive API/service behavior only.
- **II. Powerful Runtime Configurability**: PASS. Runtime behavior remains driven by existing worker token + payload contracts; no hidden mode coercion.
- **III. Modular and Extensible Architecture**: PASS. Changes stay in existing router/schema/service boundaries without cross-module coupling.
- **IV. Avoid Exclusive Proprietary Vendor Lock-In**: PASS. Secret and state callback contracts remain provider-neutral and portable.
- **V. Self-Healing by Default**: PASS. State callback persistence enables deterministic resume/checkpoint behavior for worker retries.
- **VI. Facilitate Continuous Improvement**: PASS. Unit coverage + deterministic error responses improve operator feedback loops.
- **VII. Spec-Driven Development Is the Source of Truth**: PASS. Plan, research, data model, contracts, quickstart, and tasks remain synchronized to `DOC-REQ-*`.
- **VIII. Skills Are First-Class and Easy to Add**: PASS. Worker-facing contract changes do not alter skill packaging or execution contracts.

### Post-Design Re-Check

- PASS. Phase 0/1 artifacts (`research.md`, `data-model.md`, `contracts/`, `quickstart.md`) resolve unknowns and preserve scoped runtime behavior.
- PASS. Runtime-mode guard remains explicit: docs-only completion is invalid for this feature.
- PASS. No constitution violations require Complexity Tracking exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/030-manifest-phase1/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── manifest-phase1.openapi.yaml
│   └── requirements-traceability.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
│   ├── agent_queue.py
│   └── manifests.py
├── api/schemas.py
└── services/manifests_service.py

tests/
├── unit/api/routers/test_agent_queue.py
├── unit/api/routers/test_manifests.py
└── unit/services/test_manifests_service.py
```

**Structure Decision**: Reuse existing API routers and manifest service boundaries; no new service or schema migration is required for Phase 1.

## Phase 0 - Research Summary

`specs/030-manifest-phase1/research.md` confirms:

1. Secret resolution must be worker-token + capability + ownership gated.
2. `manifestSecretRefs` is the only lookup contract source; inline manifest parsing is out of scope.
3. Profile secret resolution should reuse `AuthProviderManager` with requester context.
4. Worker checkpoint writes should reuse existing registry columns and fail fast on unresolved refs.

## Phase 1 - Design Outputs

- `data-model.md` defines resolution request/response and state callback entities.
- `contracts/manifest-phase1.openapi.yaml` captures endpoint contract semantics.
- `contracts/requirements-traceability.md` maps every `DOC-REQ-*` to implementation and validation surfaces.
- `quickstart.md` defines deterministic runtime validation flow.

## Implementation Strategy

### 1. Secret Resolution Endpoint Hardening

- Keep `POST /api/queue/jobs/{jobId}/manifest/secrets` worker-token only.
- Enforce `manifest` capability and `job.type == manifest`, `job.status == running`, `job.claimed_by == worker`.
- Resolve profile refs via `AuthProviderManager`; return vault refs as metadata-only pass-through.

### 2. Manifest State Callback Persistence

- Keep `ManifestStateUpdateRequest` and `POST /api/manifests/{name}/state` as worker callback interface.
- Persist `state_json`, `state_updated_at`, and optional `last_run_*` fields via `ManifestsService.update_manifest_state(...)`.

### 3. Validation Coverage

- Router tests validate success + denied states for secret resolution.
- Service/router tests validate state callback persistence and not-found behavior.
- End-to-end unit gate is `./tools/test_unit.sh`.

### 4. Runtime-vs-Docs Mode Alignment Gate

- Current orchestration intent is runtime implementation.
- Tasks must retain runtime code + tests; docs/spec-only task sets are non-compliant.
- Validation scope checks should stay in runtime mode behavior for this feature.

## Prompt B Remediation Application (Step 12/16)

### Completed CRITICAL/HIGH remediations

- Runtime mode scope gate is explicitly satisfied by production runtime code tasks (`T001-T004`, `T006-T007`, `T010`) and validation tasks (`T005`, `T008-T009`, `T011-T013`) in `tasks.md`.
- `DOC-REQ-*` traceability includes deterministic implementation-task and validation-task mappings for `DOC-REQ-001` through `DOC-REQ-004` in `contracts/requirements-traceability.md`.
- Cross-artifact determinism is preserved: spec runtime intent, plan constraints, and task execution coverage align without contradictory docs-only scope language.

### Completed MEDIUM/LOW remediations

- Added explicit Prompt B scope controls section in `tasks.md` so runtime and validation expectations stay auditable.
- Normalized schema ownership references in tasks/traceability to match the current codebase (`moonmind/schemas/agent_queue_models.py` and `api_service/api/schemas.py`).

### Residual risks

- Deferred `manifest_v0` runtime execution remains an integration dependency for future phases.
- Runtime secret availability still depends on profile provider data quality; unresolved refs intentionally fail fast to protect execution correctness.

## Risks & Mitigations

- **Secret leakage risk**: mitigate with capability + ownership guards and denied-path tests.
- **Manifest state drift**: mitigate with explicit callback schema and timestamp/run-metadata persistence tests.
- **Scope creep into full manifest engine**: mitigate by keeping non-goals explicit (`manifest_v0` executor deferred).

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
