# Implementation Plan: Task Editing System

**Branch**: `042-task-editing-system` | **Date**: 2026-02-26 | **Spec**: `specs/042-task-editing-system/spec.md`  
**Input**: Feature specification from `/specs/042-task-editing-system/spec.md`

## Summary

Implement queued task editing end-to-end by reusing the existing queue-create contract/UI shape while updating an eligible queued task job in place, preserving the same job ID, enforcing row-lock + optimistic-concurrency safety, and appending auditable update events. The selected orchestration mode is runtime (not docs-only), so completion requires production runtime code changes plus validation tests.

## Technical Context

**Language/Version**: Python 3.11 backend services + vanilla JavaScript dashboard  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async repository/service stack, existing queue payload normalization helpers, dashboard runtime config/view-model plumbing  
**Storage**: PostgreSQL `agent_jobs` + `agent_job_events` (reuse existing mutable columns and event append path; no new schema for v1 edits)  
**Testing**: `./tools/test_unit.sh` (Python unit tests + dashboard JS tests)  
**Target Platform**: Linux/Docker Compose MoonMind API + worker + `/tasks/*` dashboard routes  
**Project Type**: Multi-service backend + static frontend dashboard  
**Performance Goals**: Maintain queue update as a single-row transactional mutation; preserve queue list/detail responsiveness by reusing existing detail fetch/update flows; avoid introducing extra polling loops  
**Constraints**: Editability is limited to `type=task` + `status=queued` + `startedAt=null`; preserve job ID; enforce optimistic concurrency when `expectedUpdatedAt` is supplied; keep attachment mutation out of v1 scope; keep runtime-vs-docs behavior aligned with runtime implementation intent  
**Scale/Scope**: All user-owned queued task jobs surfaced through queue detail/edit flows, including service/router/dashboard behavior and validation coverage for conflict/error paths

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Deployment with Smart Defaults**: PASS. Change is additive within existing services and does not add new mandatory secrets or bootstrap steps.
- **II. Powerful Runtime Configurability**: PASS. Runtime gate behavior and dashboard endpoint templates remain configuration-driven; no hardcoded environment-specific behavior is introduced.
- **III. Modular and Extensible Architecture**: PASS. Work stays inside existing queue service/repository/router/UI module boundaries.
- **IV. Avoid Exclusive Proprietary Vendor Lock-In**: PASS. Queue update contract is runtime-agnostic and reuses normalized task payload patterns.
- **V. Self-Healing by Default**: PASS. Row-lock/state validation and conflict semantics preserve deterministic behavior under claim/update races.
- **VI. Facilitate Continuous Improvement**: PASS. Update audit events and normalized errors preserve operator visibility for post-run analysis.
- **VII. Spec-Driven Development Is the Source of Truth**: PASS. `DOC-REQ-*` mapping and plan artifacts are explicitly generated for this feature.
- **VIII. Skills Are First-Class and Easy to Add**: PASS. Skill payload fields remain part of normalized queue payloads; update flow does not bypass skill contracts.

### Post-Design Re-Check

- PASS. Phase 1 outputs (`research.md`, `data-model.md`, `contracts/`, `quickstart.md`) keep scope additive, runtime-mode aligned, and within existing modular boundaries.
- PASS. No constitution violations require Complexity Tracking exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/042-task-editing-system/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── task-editing.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/agent_queue_models.py
└── workflows/agent_queue/
    ├── repositories.py
    ├── service.py
    └── task_contract.py

api_service/
├── api/routers/
│   ├── agent_queue.py
│   └── task_dashboard_view_model.py
└── static/task_dashboard/dashboard.js

docs/
├── TaskEditingSystem.md
├── TaskQueueSystem.md
└── TaskUiArchitecture.md

tests/
├── unit/workflows/agent_queue/test_service_update.py
├── unit/api/routers/test_agent_queue.py
├── unit/api/routers/test_task_dashboard_view_model.py
└── task_dashboard/test_submit_runtime.js
```

**Structure Decision**: Reuse existing queue stack and dashboard route surfaces; do not introduce new services, persistence tables, or UI frameworks for v1 task editing.

## Phase 0 – Research Summary

Research outcomes in `specs/042-task-editing-system/research.md` establish:

1. In-place queued update (same job ID) as the canonical implementation path.
2. Locking + status/started checks to preserve claim/update race safety.
3. Optional optimistic concurrency (`expectedUpdatedAt`) for stale-tab protection.
4. Error normalization and runtime-gate handling parity with create flows.
5. Runtime-vs-docs orchestration mode alignment for this feature (runtime implementation mandatory).

## Phase 1 – Design Outputs

- **Data Model**: `data-model.md` defines editability snapshot, update request/mutation, audit event payload, and UI edit-session state.
- **API Contract**: `contracts/task-editing.openapi.yaml` defines update request/response/error semantics and detail-fetch dependency for edit prefill.
- **Traceability**: `contracts/requirements-traceability.md` maps every `DOC-REQ-*` to FRs, planned implementation surfaces, and validation strategy.
- **Execution Guide**: `quickstart.md` defines runtime-mode implementation flow and validation commands.

## Implementation Strategy

### 1. Queue API and Schema Contract

- Keep update request fields aligned with create envelope (`type`, `priority`, `maxAttempts`, `affinityKey`, `payload`) and add optional `expectedUpdatedAt` + `note`.
- Ensure `PUT /api/queue/jobs/{jobId}` returns `JobModel` on success.
- Normalize update failures to documented semantics: `404`, `403`, `409`, `422`, and runtime-gate `400`.

### 2. Service + Repository Mutation Flow

- Use row-lock retrieval (`require_job_for_update`) before mutation.
- Enforce owner authorization and editability invariants (`type=task`, queued, never started).
- Reuse task payload normalization from create path.
- Update mutable job fields in place, refresh `updated_at`, append `Job updated` audit event, commit transaction.

### 3. Dashboard Edit-Mode Reuse

- Use `/tasks/queue/new?editJobId=<jobId>` as the canonical edit entry route, preserving the existing `/tasks/new` alias behavior.
- Prefill edit form from queue detail API response.
- Switch primary CTA to `Update`; on cancel, return to queue detail route.
- Submit update requests through configured `sources.queue.update` template and include cached `expectedUpdatedAt` when available.

### 4. Documentation and API-Surface Visibility

- Keep queue-system and UI-architecture docs aligned with update endpoint availability.
- Document v1 non-goal boundaries (no attachment mutation via edit flow).

### 5. Validation Strategy

- Service tests cover success + conflict/authorization/concurrency rejections.
- Router tests cover HTTP mapping (`200/400/403/404/409/422`) for update endpoint.
- Dashboard tests cover edit query parsing, editability gating, update payload behavior, and endpoint-template usage.
- Execute `./tools/test_unit.sh` as the required validation gate.

### 6. Runtime-vs-Docs Mode Alignment Gate

- This feature is runtime-scoped (`Implementation Intent: Runtime implementation`).
- Planning and subsequent tasks must include production code/test work; docs/spec-only completion is invalid.
- Runtime scope checks should be applied during implementation (`validate-implementation-scope.sh --mode runtime`).

## Remediation Gates (Prompt B)

- `DOC-REQ-*` coverage must remain complete: each requirement needs at least one planned implementation surface and one validation strategy.
- Runtime mode requires tasks to include both implementation and validation entries; docs-only task sets fail acceptance.
- v1 non-goals (running-job edits, orchestrator edits, attachment edits) must remain explicit and enforced.
- `spec.md`, `plan.md`, `tasks.md`, and `contracts/requirements-traceability.md` must use consistent route/file naming (including `task_dashboard_view_model.py`) and deterministic `DOC-REQ-*` mappings.

## Risks & Mitigations

- **Claim/update race regressions**: enforce lock-first update + status/started validation and test conflict paths.
- **Stale-tab overwrites**: preserve optional `expectedUpdatedAt` check and explicit `409` conflict messaging.
- **UI/API contract drift**: keep update request schema mirrored with create envelope and assert behavior in router + dashboard tests.
- **Scope creep into attachments or orchestrator edits**: enforce type/non-goal guards in service and traceability gates.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
