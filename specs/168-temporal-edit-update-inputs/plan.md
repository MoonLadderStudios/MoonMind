# Implementation Plan: Temporal Edit UpdateInputs

**Branch**: `168-temporal-edit-update-inputs` | **Date**: 2026-04-13 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/168-temporal-edit-update-inputs/spec.md`

## Summary

Implement the runtime Phase 3 slice for Temporal-native task editing by enabling active `MoonMind.Run` edit submissions from the shared `/tasks/new` edit mode. The implementation reuses the Phase 1 capability/read contract and Phase 2 draft reconstruction work, prepares artifact-safe edited input payloads, submits `UpdateInputs` to the existing execution update endpoint, handles accepted/deferred/rejected backend outcomes explicitly, and returns operators to the Temporal detail experience without queue-era fallback.

## Technical Context

**Language/Version**: TypeScript/React frontend, Python 3.12 backend contracts and Temporal service  
**Primary Dependencies**: React, TanStack Query, Vitest, FastAPI/Pydantic execution models, Temporal execution update APIs, artifact create/upload APIs  
**Storage**: Existing immutable artifact storage and Temporal execution projections; no new persistence tables  
**Testing**: Vitest for frontend helper/page/detail behavior, TypeScript typecheck, targeted Python unit/contract tests where backend contract behavior changes, and `./tools/test_unit.sh` for final verification  
**Target Platform**: MoonMind Mission Control and API service in the existing Docker Compose deployment  
**Project Type**: Single repository with frontend entrypoints, shared frontend helpers, backend API schemas/services, and unit/contract tests  
**Performance Goals**: Edit submit performs at most one artifact creation/upload sequence when externalization is required and one execution update request; small inline edits avoid unnecessary artifact creation unless replacing a historical artifact-backed input  
**Constraints**: Runtime implementation required; `MoonMind.Run` active-edit only; `temporalTaskEditing` feature flag and backend capability gated; `UpdateInputs` only; no rerun submission; no `editJobId`, `/tasks/queue/new`, queue update route, queue route, or queue resubmit fallback; historical artifacts must not be mutated; failure states must remain explicit  
**Scale/Scope**: One shared `/tasks/new` edit surface, one supported workflow type, supported first-slice task fields reconstructed by Phase 2, local mocked fixtures and unit tests; no proposal editing, recurring schedule editing, terminal rerun, legacy queue jobs, or non-`MoonMind.Run` workflows

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The feature uses the existing Temporal execution update contract rather than creating a separate editing engine.
- **II. One-Click Agent Deployment**: PASS. No new services, secrets, images, or deployment prerequisites are introduced.
- **III. Avoid Vendor Lock-In**: PASS. Edited input state is MoonMind/Temporal task data and does not bind behavior to a specific agent provider.
- **IV. Own Your Data**: PASS. Edited inputs and artifacts remain in operator-controlled MoonMind storage.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill/template selections remain data in the task input patch rather than hardcoded workflow behavior.
- **VI. The Bittersweet Lesson**: PASS. The implementation is a thin submit/payload layer around existing read, draft, artifact, and update contracts with focused tests.
- **VII. Powerful Runtime Configurability**: PASS. The existing runtime-visible `temporalTaskEditing` flag gates the feature.
- **VIII. Modular and Extensible Architecture**: PASS. Work is scoped to the shared task-create entrypoint, task-detail notice behavior, frontend helper contracts, and existing execution update API surfaces.
- **IX. Resilient by Default**: PASS. Submit-time rejection and stale state are handled explicitly; backend capability flags are not treated as permanent guarantees.
- **X. Facilitate Continuous Improvement**: PASS. Operator-readable outcomes and tests make failures diagnosable.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This plan is generated from the feature spec and maps validation to the specified outcomes.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Migration/implementation details stay under `specs/` artifacts, not canonical docs.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The plan refuses queue-era aliases/fallbacks instead of preserving compatibility shims.

## Project Structure

### Documentation (this feature)

```text
specs/168-temporal-edit-update-inputs/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
├── contracts/
│   └── temporal-edit-update-inputs.openapi.yaml
└── tasks.md              # Generated later by speckit-tasks, not by speckit-plan
```

### Source Code (repository root)

```text
frontend/
└── src/
    ├── entrypoints/
    │   ├── task-create.tsx
    │   ├── task-create.test.tsx
    │   ├── task-detail.tsx
    │   └── task-detail.test.tsx
    └── lib/
        └── temporalTaskEditing.ts

api_service/
└── api/
    └── routers/
        ├── executions.py
        └── task_dashboard_view_model.py

moonmind/
├── schemas/
│   └── temporal_models.py
└── workflows/
    └── temporal/
        ├── service.py
        └── workflows/
            └── run.py

tests/
├── unit/
│   ├── api/
│   │   └── routers/
│   │       └── test_executions.py
│   └── workflows/
│       └── temporal/
│           ├── test_temporal_service.py
│           └── workflows/
│               └── test_run_signals_updates.py
└── contract/
    └── test_temporal_execution_api.py
```

**Structure Decision**: Use the existing Mission Control task-create form, shared Temporal task editing helper, Temporal detail page, artifact APIs, and execution update endpoint. Backend changes are expected only if validation exposes gaps in the existing `UpdateInputs` contract or response semantics.

## Phase 0: Research Summary

Research is captured in [research.md](research.md). Decisions:

1. Reuse the canonical execution update endpoint and `UpdateInputs` request envelope.
2. Build edit submit payloads from the same form payload used for create, but route them as `parametersPatch` instead of creating a new execution.
3. Create a fresh input artifact when replacing artifact-backed historical input or when edited input exceeds inline limits.
4. Treat backend capability flags as load-time gates and backend update responses as submit-time authority.
5. Preserve rerun as an explicit later-phase path and keep queue-era routes/params absent from edit submission.

## Phase 1: Design Outputs

- [data-model.md](data-model.md): Defines editable execution, edited input state, artifact edit payload, update request, update outcome, and operator notice state.
- [contracts/temporal-edit-update-inputs.openapi.yaml](contracts/temporal-edit-update-inputs.openapi.yaml): Captures the execution update request/response and artifact creation surfaces consumed by edit submit.
- [quickstart.md](quickstart.md): Provides deterministic validation commands for the runtime implementation.

## Implementation Strategy

### 1. Payload Builder and Artifact Rules

- Add or extend a frontend helper that builds the canonical edit update payload with `updateName = "UpdateInputs"`, `parametersPatch`, and optional `inputArtifactRef`.
- Reuse the existing task form payload construction so supported edited fields are preserved.
- Apply existing artifact externalization policy for oversized edited input content.
- Force a new artifact reference when editing a task that was reconstructed from a historical input artifact, even if the edited content could otherwise fit inline.
- Never mutate, overwrite, or reuse the historical artifact as the edited input reference.

### 2. Edit Submit Handling

- Remove the Phase 2 edit-mode non-submitting guard while keeping rerun non-submitting for this feature.
- In edit mode, post the prepared payload to the configured execution update endpoint for the current workflow identifier.
- Send `UpdateInputs` only; do not route through create, rerun, or queue-era submission.
- Preserve create mode behavior and keep rerun submission blocked until the rerun feature phase.

### 3. Success and Redirect UX

- Interpret accepted backend outcomes and display outcome-specific copy for immediate application, safe-point scheduling, and continue-as-new style handling.
- Store or carry the one-time success message across navigation to the Temporal detail page.
- Redirect to the Temporal detail route for the relevant execution context after success.
- Ensure the detail page refresh/refetch path remains active after navigation so operators see current state.

### 4. Failure UX and Submit-Time Revalidation

- Surface backend validation and stale-state rejection messages directly to the operator.
- Keep the operator in edit mode when artifact preparation, validation, capability, or stale-state failures occur.
- Do not show success messages or redirect after rejected updates.
- Keep feature-flag-disabled, unsupported workflow type, and missing-capability errors from Phase 2 intact before submit.

### 5. Validation

- Add focused Vitest coverage for the payload builder, active edit submit, artifact-backed edit submit, stale-state rejection, and detail-page success notice.
- Add backend unit or contract coverage only if implementation changes backend update validation/response behavior.
- Run TypeScript typecheck and the full unit runner before completion.

## Post-Design Constitution Re-Check

- **I. Orchestrate, Don't Recreate**: PASS. The design uses the existing Temporal update model.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisites change.
- **III. Avoid Vendor Lock-In**: PASS. Provider/runtime/model values are data copied through existing contracts.
- **IV. Own Your Data**: PASS. Edited artifacts remain in MoonMind artifact storage and historical artifacts remain immutable.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill and template state flow as editable task input data.
- **VI. The Bittersweet Lesson**: PASS. The helper/page split keeps the slice replaceable and testable.
- **VII. Powerful Runtime Configurability**: PASS. `temporalTaskEditing` remains the rollout control.
- **VIII. Modular and Extensible Architecture**: PASS. Changes are scoped to existing UI/helper/API boundaries.
- **IX. Resilient by Default**: PASS. Submit-time rejection is explicit and stale capability state is expected.
- **X. Facilitate Continuous Improvement**: PASS. Success/failure messages and tests improve diagnosis.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Plan, research, data model, contracts, and quickstart align to the spec.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. No canonical docs are changed for migration tracking.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. No queue-era compatibility alias or fallback is planned.

## Complexity Tracking

No constitution violations or complexity exceptions.
