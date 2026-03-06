# Implementation Plan: Executions API Contract Runtime Delivery

**Branch**: `048-executions-api-contract` | **Date**: 2026-03-06 | **Spec**: `specs/048-executions-api-contract/spec.md`  
**Input**: Feature specification from `/specs/048-executions-api-contract/spec.md`

## Summary

Implement `docs/Api/ExecutionsApiContract.md` as a runtime-authoritative `/api/executions` surface backed by the existing Temporal execution projection and lifecycle service. The plan hardens router/schema/service behavior, preserves `workflowId` as the durable handle, keeps task-facing compatibility adapters stable during migration, and requires automated validation tests so completion cannot regress into docs-only scope.

## Technical Context

**Language/Version**: Python 3.11, OpenAPI YAML, small dashboard JavaScript touchpoints for compatibility behavior  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, existing Temporal execution runtime in `moonmind/workflows/temporal`, dashboard task view-model helpers  
**Storage**: PostgreSQL/SQLite-backed `temporal_executions` projection table plus JSON search-attribute/memo fields; artifact references remain strings into the existing artifact system  
**Testing**: `./tools/test_unit.sh`, contract coverage in `tests/contract/test_temporal_execution_api.py`, router coverage in `tests/unit/api/routers/test_executions.py`, service coverage in `tests/unit/workflows/temporal/test_temporal_service.py`, DOC-REQ traceability coverage in `tests/unit/specs/test_doc_req_traceability.py`, compatibility assertions in dashboard/task adapter tests as needed  
**Target Platform**: Linux containerized MoonMind API + worker runtime with authenticated HTTP clients and task dashboard consumers  
**Project Type**: Backend API contract and runtime lifecycle feature with light compatibility-adapter UI surface checks  
**Performance Goals**: Keep list pagination deterministic and efficient on the existing projection indexes, preserve opaque cursor behavior, and avoid adding extra network round-trips or non-indexed visibility lookups for the core six routes  
**Constraints**: Runtime implementation mode is mandatory; JSON remains camelCase; `workflowId` is canonical while `runId` is non-durable detail; `/api/executions` must not expose `taskId`; cross-owner direct access must stay non-disclosing; future backend swaps must not alter the external response contract  
**Scale/Scope**: Complete the contract for create/list/describe/update/signal/cancel across router, schemas, service, projection model, migration compatibility surfaces, and validation tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. The feature lands inside existing API/runtime/test paths and keeps Docker Compose startup unchanged.
- **II. Avoid Vendor Lock-In**: PASS. `/api/executions` remains an adapter contract over Temporal-backed runtime details rather than exposing vendor-specific server APIs.
- **III. Own Your Data**: PASS. Execution metadata, memo, and artifact refs stay in MoonMind-managed storage and portable JSON structures.
- **IV. Skills Are First-Class and Easy to Add**: PASS. The contract does not change skill authoring semantics and stays compatible with existing workflow orchestration.
- **V. Design for Replaceability**: PASS. The external execution JSON contract is explicitly separated from the current projection-backed implementation.
- **VI. Powerful Runtime Configurability**: PASS. Namespace and Continue-As-New thresholds remain settings-driven rather than hardcoded per route.
- **VII. Modular and Extensible Architecture**: PASS. Changes stay localized to `api_service/api/routers/executions.py`, `moonmind/schemas/temporal_models.py`, `moonmind/workflows/temporal/service.py`, projection models, and compatibility adapters.
- **VIII. Self-Healing by Default**: PASS. Create/update idempotency and terminal-state handling remain explicit and retry-safe.
- **IX. Facilitate Continuous Improvement**: PASS. Structured errors, lifecycle summaries, and validation tests improve diagnosability for operators and future migration work.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. Every `DOC-REQ-*` requirement is mapped into traceability and planned validation.

### Post-Design Re-Check

- PASS. Phase 1 artifacts keep `/api/executions` contract-first while allowing backing implementation changes.
- PASS. Runtime mode remains the governing completion gate: production code and automated validation tests are required.
- PASS. No constitution violations require exceptions in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/048-executions-api-contract/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── executions-api-contract.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
docs/Api/ExecutionsApiContract.md
docs/Temporal/VisibilityAndUiQueryModel.md
docs/Temporal/RunHistoryAndRerunSemantics.md

api_service/
├── api/routers/
│   ├── executions.py
│   └── task_dashboard_view_model.py
├── db/
│   └── models.py
├── main.py
└── static/task_dashboard/
    └── dashboard.js

moonmind/
├── schemas/
│   └── temporal_models.py
└── workflows/
    └── temporal/
        ├── __init__.py
        └── service.py

tests/
├── contract/
│   └── test_temporal_execution_api.py
├── unit/api/routers/
│   ├── test_executions.py
│   └── test_task_dashboard_view_model.py
└── unit/workflows/temporal/
    └── test_temporal_service.py
```

**Structure Decision**: Keep the implementation inside the existing Temporal execution router/schema/service/projection stack and extend the already-present contract/unit tests. Compatibility behavior for task-facing consumers stays in current adapter/view-model surfaces rather than introducing a second execution API.

## Phase 0 - Research Summary

Research outcomes in `specs/048-executions-api-contract/research.md` establish:

1. `/api/executions` stays an execution-oriented adapter contract even while the runtime remains projection-backed.
2. `workflowId` is the only durable execution identifier on this API; `taskId == workflowId` survives only in compatibility adapters.
3. Projection-backed exact counts and offset-based cursors may continue internally, but `nextPageToken` remains opaque and `countMode` remains explicit.
4. Owner/admin authorization and non-disclosing `404` behavior belong at the router boundary, with service logic remaining contract-focused.
5. Create/update idempotency semantics stay intentionally narrow and explicit instead of expanding into hidden replay ledgers.
6. Response envelopes must preserve documented baseline search attributes, memo keys, and domain error codes while tolerating additive metadata.
7. Rerun and some large updates remain Continue-As-New style operations that preserve `workflowId` and rotate `runId`.
8. Runtime mode is mandatory: the feature is incomplete without production code and automated validation tests.

## Phase 1 - Design Outputs

- **Research**: `research.md` records the identifier, pagination, authorization, idempotency, compatibility, and runtime-mode decisions that remove ambiguity before implementation.
- **Data Model**: `data-model.md` defines execution identity, projection state, API request/response envelopes, compatibility adapter behavior, and invariant rules.
- **API Contract**: `contracts/executions-api-contract.openapi.yaml` defines the six `/api/executions` routes, schemas, status codes, and structured domain error envelopes.
- **Traceability**: `contracts/requirements-traceability.md` maps `DOC-REQ-001` through `DOC-REQ-015` to FRs, planned runtime surfaces, and validation strategy.
- **Execution Guide**: `quickstart.md` documents runtime-mode validation flow, repository-standard test execution, and implementation-scope gates.

## DOC-REQ Coverage Summary

- `DOC-REQ-001`, `DOC-REQ-014`: Preserve `/api/executions` as the execution-oriented adapter, keep migration compatibility explicit, and hold the external execution JSON shape stable across adapter changes.
- `DOC-REQ-002`, `DOC-REQ-005`, `DOC-REQ-006`: Keep `workflowId` canonical, preserve camelCase `ExecutionModel` / `ExecutionListResponse` serialization, and materialize baseline search attribute and memo metadata without exposing `taskId`.
- `DOC-REQ-003`, `DOC-REQ-009`, `DOC-REQ-013`: Enforce authenticated owner/admin access, non-disclosing hidden-resource behavior, and stable domain error envelopes at the router boundary.
- `DOC-REQ-004`, `DOC-REQ-007`, `DOC-REQ-008`: Constrain workflow/state enums, create validation and idempotency, and deterministic list filtering, ordering, pagination, and count semantics in the service/projection layer.
- `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-012`: Deliver contract-compliant update, signal, rerun, and cancel lifecycle controls with documented terminal-state and response semantics.
- `DOC-REQ-015`: Require production runtime code changes, automated validation suites, runtime-scope gates, and machine-verifiable traceability coverage before the feature is complete.

## Implementation Strategy

### 1. Harden shared execution schemas and serialization

- Align `moonmind/schemas/temporal_models.py` with the documented request/response shapes for create, list, describe, update, signal, and cancel.
- Keep camelCase JSON aliases authoritative and preserve `ExecutionModel` / `ExecutionListResponse` as the only direct router payloads for this API.
- Normalize structured domain error envelopes in router responses so `detail.code` and `detail.message` stay stable even when framework validation errors occur outside that contract.

### 2. Preserve identifier and visibility invariants in the projection/service layer

- Keep `api_service/db/models.py::TemporalExecutionRecord` as the current materialized execution row, but treat it as an internal adapter detail rather than the public abstraction.
- Ensure `moonmind/workflows/temporal/service.py` preserves `workflowId` as the durable identity, rotates `runId` on rerun/Continue-As-New behavior, and never leaks `taskId` through execution responses.
- Materialize baseline `searchAttributes` and `memo` keys with real owner metadata on authenticated create paths while leaving both objects extensible for future additive keys.

### 3. Complete create/list/describe route contract behavior

- Keep `POST /api/executions` responsible for supported workflow validation, manifest requirements, JSON-serializable initial parameter handling, and `(ownerId, workflowType, idempotencyKey)` create deduplication.
- Keep `GET /api/executions` responsible for owner scoping, admin-only cross-owner list filtering, deterministic ordering (`updatedAt DESC`, then `workflowId DESC`), opaque pagination tokens, and explicit `countMode`.
- Keep `GET /api/executions/{workflowId}` returning `execution_not_found` for both missing and ownership-hidden records.

### 4. Tighten lifecycle mutation semantics for update, signal, and cancel

- Preserve `UpdateInputs`, `SetTitle`, and `RequestRerun` as the only supported update names, with narrow most-recent-key idempotency and terminal-state `accepted=false` behavior.
- Preserve `ExternalEvent`, `Approve`, `Pause`, and `Resume` as the only supported signals, with `409 signal_rejected` for terminal or invalid signal requests.
- Preserve graceful cancel vs forced termination semantics, including `state`/`closeStatus` mapping, unchanged terminal cancel returns, and summary updates.

### 5. Keep migration compatibility explicit

- Treat `/api/executions` as the execution-shaped source of truth while task-oriented dashboard or compatibility surfaces continue adapting execution data for users during migration.
- Preserve the fixed bridge `taskId == workflowId` in compatibility adapters and tests without adding `taskId` to the direct execution API.
- Audit existing dashboard/task adapter behavior in `api_service/static/task_dashboard/dashboard.js` and adjacent view-model helpers so compatibility consumers keep working when execution responses harden around `workflowId`.

### 6. Validation strategy

- Extend `tests/contract/test_temporal_execution_api.py` to cover all six routes, required fields, success statuses, count behavior, authorization rules, rerun identity invariants, and cancel semantics.
- Extend `tests/unit/workflows/temporal/test_temporal_service.py` for create validation, idempotency, state transitions, signal/update rules, and pagination/count mechanics.
- Extend `tests/unit/api/routers/test_executions.py` for structured domain errors, hidden-resource behavior, and auth-scope enforcement.
- Keep `tests/unit/specs/test_doc_req_traceability.py` passing so `DOC-REQ-001` through `DOC-REQ-015` stay machine-verifiable for the active feature.
- Add or extend compatibility assertions in dashboard/task adapter tests where needed so the migration bridge `taskId == workflowId` remains explicit and non-regressing.
- Run repository-standard acceptance through `./tools/test_unit.sh`.

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode**.
- Completion requires production runtime code changes plus automated validation tests.
- Docs, specs, and contracts are supporting artifacts only and do not satisfy the delivery gate for this feature.

## Remediation Gates

- Every `DOC-REQ-*` row must remain mapped to FRs, runtime implementation surfaces, and planned validation strategy in `contracts/requirements-traceability.md`.
- `workflowId` must remain the canonical execution identifier on `/api/executions`; any `taskId` bridge logic must stay outside this API surface.
- Pagination tokens must remain opaque even if the backing implementation continues using offset semantics internally.
- Owner/admin authorization and non-disclosing `404` behavior must stay explicit and test-covered.
- Planning is invalid if runtime-mode requirements are weakened into docs-only completion.

## Risks & Mitigations

- **Risk: contract drift between source doc, router schemas, and service behavior.**
  - **Mitigation**: keep the OpenAPI contract, traceability matrix, and contract tests aligned to the same six-route surface.
- **Risk: migration adapters keep relying on `taskId` or `runId` in ways that conflict with the execution contract.**
  - **Mitigation**: make the compatibility bridge explicit in plan/tasks/tests and audit dashboard adapter fallbacks.
- **Risk: current projection-backed exact counts and cursor behavior are mistaken for a permanent backend guarantee.**
  - **Mitigation**: keep `countMode` explicit, keep `nextPageToken` opaque, and document the adapter boundary in both contract and tests.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
