# Implementation Plan: Workflow Type Catalog and Lifecycle

**Branch**: `046-workflow-type-lifecycle` | **Date**: 2026-03-05 | **Spec**: `specs/046-workflow-type-lifecycle/spec.md`  
**Input**: Feature specification from `/specs/046-workflow-type-lifecycle/spec.md`

## Summary

Implement the Temporal workflow type catalog and lifecycle contract from `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` as runtime-authoritative behavior. The plan delivers production code and validation tests for: fixed v1 workflow types, `mm_state` lifecycle semantics, visibility/memo schema, update/signal/cancel/rerun controls, Continue-As-New policy enforcement, timeout/retry and error taxonomy behavior, and owner/admin authorization invariants.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, existing MoonMind Temporal settings/service stack (`moonmind/workflows/temporal`)  
**Storage**: PostgreSQL projection table (`temporal_executions`) for lifecycle/materialized visibility contract; artifact references for large payloads  
**Testing**: `./tools/test_unit.sh`, contract tests in `tests/contract/test_temporal_execution_api.py`, unit tests in `tests/unit/workflows/temporal/test_temporal_service.py`, Temporal integration suites under `tests/integration/temporal/`  
**Target Platform**: Linux containerized MoonMind API/worker runtime with Temporal-backed orchestration  
**Project Type**: Backend runtime + API contract + persistence model updates  
**Performance Goals**: Deterministic lifecycle transitions, bounded history growth via Continue-As-New thresholds, visibility-backed list/filter behavior with stable pagination tokens  
**Constraints**: Preserve Temporal-first semantics; keep large payloads out of workflow history/memo; enforce owner/admin control authorization; keep runtime vs docs mode aligned to runtime implementation mode  
**Scale/Scope**: Workflow catalog/lifecycle runtime implementation in temporal service/router/schema/model layers with validation tests for contracts, invariants, and failure paths

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. Feature ships inside existing Python/API/test workflows without new operator prerequisites.
- **II. Avoid Vendor Lock-In**: PASS. Contracts use portable JSON payloads and artifact references; no provider-locked product semantics are introduced.
- **III. Own Your Data**: PASS. Visibility metadata and memo fields stay inspectable and portable.
- **IV. Skills Are First-Class and Easy to Add**: PASS. Workflow orchestration keeps side effects in activity boundaries and remains compatible with existing skill runtime model.
- **V. Design for Replaceability**: PASS. Lifecycle behaviors are explicit contracts in schema/service layers, not hidden adapter logic.
- **VI. Powerful Runtime Configurability**: PASS. Continue-As-New thresholds and namespace inputs remain runtime-configurable via settings.
- **VII. Modular and Extensible Architecture**: PASS. Changes are localized to temporal workflow modules, API router/contracts, and tests.
- **VIII. Self-Healing by Default**: PASS. Idempotent updates/reruns and explicit terminal-state handling preserve retry-safe behavior.
- **IX. Facilitate Continuous Improvement**: PASS. Structured lifecycle/memo outcomes improve observability and operator diagnosis.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. `DOC-REQ-*` mappings are enforced through traceability artifact coverage.

### Post-Design Re-Check

- PASS. Phase 1 artifacts keep lifecycle semantics explicit and testable.
- PASS. Runtime mode remains authoritative: completion requires runtime code + validation tests.
- PASS. No constitution violations require exceptions in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/046-workflow-type-lifecycle/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── temporal-workflow-lifecycle.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
docs/Temporal/WorkflowTypeCatalogAndLifecycle.md

moonmind/
├── config/settings.py
├── schemas/
│   └── temporal_models.py
└── workflows/
    └── temporal/
        ├── __init__.py
        └── service.py

api_service/
├── api/routers/
│   └── executions.py
├── db/models.py
└── migrations/versions/
    └── 202603050001_temporal_execution_lifecycle.py

tests/
├── contract/
│   └── test_temporal_execution_api.py
├── unit/workflows/temporal/
│   └── test_temporal_service.py
└── integration/temporal/
    ├── test_compose_foundation.py
    ├── test_namespace_retention.py
    └── test_upgrade_rehearsal.py
```

**Structure Decision**: Implement and harden lifecycle/catalog behavior in existing temporal runtime surfaces (`moonmind/workflows/temporal`, router, schema, DB model/migration) with contract + unit tests as primary validation, keeping integration tests aligned with Temporal foundation behavior.

## Phase 0 - Research Summary

Research outcomes in `specs/046-workflow-type-lifecycle/research.md` establish:

1. Fix v1 workflow type catalog to `MoonMind.Run` and `MoonMind.ManifestIngest` only.
2. Keep `mm_state` as the single required domain state filter with explicit terminal mapping.
3. Keep update contracts idempotent and rerun semantics Continue-As-New under same Workflow ID.
4. Keep signals asynchronous and authenticity checks delegated to activity-side validation paths.
5. Keep large payloads/history pressure bounded through artifact references and Continue-As-New thresholds.
6. Keep runtime mode as a hard completion gate (no docs-only closure path).

## Phase 1 - Design Outputs

- **Data Model**: `data-model.md` defines catalog entries, execution projection, visibility/memo envelopes, update/signal contracts, authorization context, and lifecycle policy entities.
- **API Contract**: `contracts/temporal-workflow-lifecycle.openapi.yaml` defines start/list/describe/update/signal/cancel contract surfaces and state/update/signal enums.
- **Traceability**: `contracts/requirements-traceability.md` maps all `DOC-REQ-001` through `DOC-REQ-018` to FRs, implementation surfaces, and validation strategy.
- **Execution Guide**: `quickstart.md` defines runtime-mode implementation/verification flow and repository-standard test command usage.

## Implementation Strategy

### 1. Catalog and identifier invariants

- Enforce v1 workflow type catalog (`MoonMind.Run`, `MoonMind.ManifestIngest`) in schema and service parsing.
- Enforce `mm:<ulid-or-uuid>` workflow ID external format and no sensitive data encoding.
- Keep reruns/restarts on same Workflow ID via Continue-As-New semantics.

### 2. Domain lifecycle state and visibility schema

- Keep `mm_state` as single required lifecycle domain state Search Attribute.
- Enforce allowed lifecycle states and terminal mapping against close status.
- Persist required indexed visibility keys (`mm_owner_id`, `mm_state`, `mm_updated_at`) and bounded memo fields (`title`, `summary`).

### 3. Update contracts and idempotency

- Implement/validate `UpdateInputs`, `SetTitle`, and `RequestRerun` as explicit updates.
- Ensure update responses include `{accepted, applied, message}` with `immediate|next_safe_point|continue_as_new` semantics.
- Preserve idempotency key handling and terminal-state rejection behavior.

### 4. Signal contracts and asynchronous external events

- Implement/validate `ExternalEvent` and `Approve` contract handling.
- Support optional `Pause`/`Resume` controls behind policy/contract boundaries.
- Route authenticity/verification requirements through activity-side validation hooks.

### 5. Cancellation, termination, and failure taxonomy

- Enforce graceful cancel -> `canceled` state/close mapping with summary updates.
- Enforce forced termination -> failed semantics with termination reason capture.
- Normalize failure categories to `user_error|integration_error|execution_error|system_error` and expose concise UI-facing summary details.

### 6. Continue-As-New and history safety

- Apply Continue-As-New thresholds for run steps and manifest wait/phase cycles.
- Preserve workflow identity and required refs across reruns/continuations.
- Keep large input/event payloads as artifact references, not expanded in memo/history.

### 7. Authorization and control-plane invariants

- Keep owner/admin authorization checks on API endpoints for updates/signals/cancel/rerun.
- Preserve defense-in-depth checks by re-validating invariants at service/workflow boundary.
- Reject unauthorized or invalid lifecycle mutations consistently.

### 8. Validation strategy

- Contract tests for lifecycle endpoints and payload/response invariants.
- Unit tests for state transitions, update/signal behavior, cancel/terminate semantics, Continue-As-New triggers, and error-category validation.
- Integration suites ensure lifecycle behaviors remain coherent with Temporal foundation assumptions.
- Repository-standard unit acceptance remains `./tools/test_unit.sh`.

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode**.
- Completion requires production runtime code updates and automated validation tests.
- Docs/spec artifacts are supporting deliverables only and cannot satisfy this feature’s completion gate.

## Remediation Gates

- Every `DOC-REQ-*` row must remain mapped to FRs, planned implementation surfaces, and validation strategy.
- Lifecycle state/filter/list behavior must remain sourced from Temporal visibility contract surfaces.
- Update/signal/cancel authorization and invariant checks must remain explicit and test-covered.
- Planning is invalid if any `DOC-REQ-*` is unmapped or lacks planned validation coverage.

## Prompt B Remediation Application (Step 12/16)

### Completed CRITICAL/HIGH remediations

- Runtime mode scope gate is explicitly satisfied by production runtime code tasks (`T001-T008`, `T013-T017`, `T021-T026`, `T030-T034`) and validation tasks (`T009-T012`, `T018-T020`, `T027-T029`, `T035-T039`) in `tasks.md`.
- `DOC-REQ-*` traceability now includes deterministic implementation-task and validation-task mappings for every source requirement (`DOC-REQ-001` through `DOC-REQ-018`) in the `DOC-REQ Coverage Matrix` in `tasks.md`.
- Cross-artifact determinism is preserved: runtime-authoritative scope and validation gate language now align across `spec.md`, `plan.md`, and `tasks.md`.

### Completed MEDIUM/LOW remediations

- Added explicit Prompt B scope controls in `tasks.md` so runtime implementation and validation expectations remain auditable before implementation starts.
- Reinforced traceability gate language in `plan.md` so any unmapped `DOC-REQ-*` is treated as a plan-invalidating condition.

### Residual risks

- Lifecycle implementation spans API router, temporal service, schemas, and persistence; semantic drift remains possible if changes bypass shared helpers and contract tests.
- Temporal integration/runtime validation depends on environment parity for integration tests; local-only coverage may not reveal all deployment-time issues.

## Risks & Mitigations

- **Risk: lifecycle drift between API contract and runtime state machine**.
  - **Mitigation**: Keep schemas and service enums aligned with contract/unit test assertions.
- **Risk: history growth from oversized payloads**.
  - **Mitigation**: enforce artifact-reference-first handling and Continue-As-New thresholds.
- **Risk: authorization regressions under mixed owner/admin flows**.
  - **Mitigation**: maintain contract tests for owner/admin restrictions and terminal-state rejection behavior.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
