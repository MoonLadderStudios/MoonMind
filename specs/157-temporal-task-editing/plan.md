# Implementation Plan: Temporal Task Editing Entry Points

**Branch**: `157-temporal-task-editing` | **Date**: 2026-04-12 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/157-temporal-task-editing/spec.md`

## Summary

Implement the runtime-scoped Phase 0 and Phase 1 slice for Temporal-native task editing. The work aligns backend and frontend contracts for supported `MoonMind.Run` executions, introduces a runtime-visible `temporalTaskEditing` rollout flag, exposes the read data and capability flags needed for later draft reconstruction, and restores the task-detail Edit and Rerun entry points without queue-era routes or semantics.

The implementation deliberately stops before full `/tasks/new` draft reconstruction and submit handling. It prepares stable runtime contracts and validates detail-page visibility and navigation so subsequent phases can add prefill and submit behavior without redefining the surface.

## Technical Context

**Language/Version**: Python 3.12 backend runtime in this workspace; TypeScript/React frontend entrypoints  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async models/projections, React, TanStack Query, Zod, Vitest, pytest  
**Storage**: Existing Temporal execution records and canonical projection rows; no new persistence tables for Phase 0/1  
**Testing**: `./tools/test_unit.sh`; targeted `pytest` for API/router coverage; targeted Vitest for task-detail UI coverage; TypeScript typecheck for frontend contracts  
**Target Platform**: MoonMind API service and Mission Control task dashboard served from the existing Docker Compose deployment  
**Project Type**: Single repository with backend service modules, frontend Mission Control entrypoints, and unit/contract tests  
**Performance Goals**: No additional polling loops; execution detail responses remain bounded to existing detail-read costs; route-helper and feature-flag evaluation are constant-time UI operations  
**Constraints**: Runtime mode requires production code and validation tests; `MoonMind.Run` only; no queue fallback; unsupported actions omitted; historical artifacts not mutated; capability flags remain authoritative; workflow/update contract changes require boundary coverage; malformed draft and unreadable artifact handling is deferred until draft reconstruction and artifact reads are implemented
**Scale/Scope**: Phase 0 and Phase 1 only: read contract scaffolding, rollout flag, route helpers, typed contracts, detail-page Edit/Rerun entry points, fixtures/tests for supported, unsupported, active, terminal, state-ineligible, missing-capability, and flag-disabled states

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The feature operates on existing Temporal execution state and workflow update names rather than creating a separate task-editing engine.
- **II. One-Click Agent Deployment**: PASS. No new external services, secrets, or deployment prerequisites are introduced.
- **III. Avoid Vendor Lock-In**: PASS. The contracts are MoonMind/Temporal execution contracts and do not bind editing to a proprietary agent provider.
- **IV. Own Your Data**: PASS. Editing data remains sourced from locally stored execution parameters and artifact references.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill/template state remains part of the draft reconstruction contract for later phases.
- **VI. The Bittersweet Lesson**: PASS. The plan adds thin contracts and focused UI entry points, leaving later submit behavior behind stable helpers.
- **VII. Powerful Runtime Configurability**: PASS. A runtime-visible `temporalTaskEditing` flag gates the new behavior.
- **VIII. Modular and Extensible Architecture**: PASS. Work is contained to Temporal API serialization/capabilities, dashboard config, task-detail UI, frontend route/contract helpers, and tests.
- **IX. Resilient by Default**: PASS. Capability flags are advisory UI gates and submit-side revalidation is preserved for later phases; no silent fallback is introduced.
- **X. Facilitate Continuous Improvement**: PASS. Unsupported reasons and fixtures make failure modes diagnosable during rollout.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This plan is generated from `spec.md`, includes traceability, and keeps implementation details in plan/design artifacts.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Runtime implementation artifacts live under `specs/`; no migration checklist is added to canonical docs.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. New primary flows do not preserve queue-era aliases or compatibility fallbacks.

## Project Structure

### Documentation (this feature)

```text
specs/157-temporal-task-editing/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
├── contracts/
│   ├── temporal-task-editing.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md              # Generated later by speckit-tasks, not by this plan
```

### Source Code (repository root)

```text
moonmind/
├── config/
│   └── settings.py
└── schemas/
    └── temporal_models.py

api_service/
└── api/
    └── routers/
        ├── executions.py
        └── task_dashboard_view_model.py

frontend/
└── src/
    ├── entrypoints/
    │   ├── task-detail.tsx
    │   └── task-detail.test.tsx
    └── lib/
        └── temporalTaskEditing.ts

tests/
└── unit/
    └── api/
        └── routers/
            ├── test_executions.py
            └── test_task_dashboard_view_model.py
```

**Structure Decision**: Use existing API/router, schema, dashboard-config, and Mission Control entrypoint boundaries. Add a small frontend helper module for canonical routes and typed editing contracts because the same helpers will be reused by `/tasks/new` mode plumbing in later phases.

## Phase 0: Research Summary

Research is captured in [research.md](research.md). Decisions:

1. Gate Edit and Rerun through both backend capability flags and the frontend runtime flag.
2. Extend the existing Temporal execution detail payload rather than creating a separate edit-read endpoint for Phase 0/1.
3. Centralize canonical `/tasks/new` route creation in a frontend helper.
4. Keep `MoonMind.Run` as the only supported workflow type in this slice.
5. Use local mocked fixtures and unit tests instead of provider/integration credentials.

## Phase 1: Design Outputs

- [data-model.md](data-model.md): Defines the execution read contract, capability set, feature flag, route targets, fixtures, and later submit update names.
- [contracts/temporal-task-editing.openapi.yaml](contracts/temporal-task-editing.openapi.yaml): Captures the execution detail, update request, and runtime config surfaces relevant to this feature.
- [contracts/requirements-traceability.md](contracts/requirements-traceability.md): Maps every `DOC-REQ-*` to functional requirements, implementation surfaces, and validation.
- [quickstart.md](quickstart.md): Provides deterministic local validation commands for the runtime implementation.

## Implementation Strategy

### 1. Backend Read Contract and Capabilities

- Extend the Temporal execution detail model to expose the current input parameters and input artifact reference needed by later draft reconstruction.
- Reuse the existing plan artifact field for run and manifest contexts rather than adding a duplicate.
- Compute `actions.canUpdateInputs` and `actions.canRerun` from workflow type, lifecycle state, existing actions enablement, and the new `temporalTaskEditing` rollout flag.
- Return explicit disabled reasons for unsupported workflow type, feature-disabled, and state-ineligible cases.
- Preserve contract hooks for later malformed-draft and unreadable-artifact failures without implementing Phase 2 draft reconstruction or artifact-read UX in this slice.

### 2. Runtime Configuration

- Add a safe default setting for `TEMPORAL_TASK_EDITING_ENABLED`.
- Surface the flag through the dashboard runtime configuration as `features.temporalDashboard.temporalTaskEditing`.
- Keep the existing `actionsEnabled` flag as the broader action transport gate while `temporalTaskEditing` gates this specific flow.

### 3. Frontend Route and Contract Scaffolding

- Add canonical helpers for create, edit, and rerun route targets.
- Add placeholder mode and execution contract types for later `/tasks/new` mode plumbing.
- Update task-detail schema parsing to understand `canUpdateInputs`.

### 4. Detail-Page Entry Points

- Render Edit only when `actions.canUpdateInputs` and `temporalTaskEditing` are both true.
- Render Rerun only when `actions.canRerun` and `temporalTaskEditing` are both true.
- Navigate through canonical links rather than directly invoking rerun or queue-era behavior from detail.
- Omit unsupported actions rather than showing misleading disabled controls.

### 5. Validation

- Backend unit coverage for read contract fields, feature-flag disabled behavior, workflow-type support, and capability disabled reasons.
- Dashboard runtime-config tests for the feature flag.
- Frontend route-helper and task-detail tests for visibility and navigation behavior.
- Full unit-test runner verification before completion.

## Post-Design Constitution Re-Check

- **I. Orchestrate, Don't Recreate**: PASS. Design keeps Temporal as source of truth.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisites changed.
- **III. Avoid Vendor Lock-In**: PASS. No provider-specific dependency introduced.
- **IV. Own Your Data**: PASS. Existing execution/artifact data remains local.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill state is represented as data, not forked behavior.
- **VI. The Bittersweet Lesson**: PASS. Thin helpers and contracts minimize replaceability cost.
- **VII. Powerful Runtime Configurability**: PASS. Dedicated runtime flag is included.
- **VIII. Modular and Extensible Architecture**: PASS. Boundaries remain API serialization, config, UI helper, UI entrypoint, tests.
- **IX. Resilient by Default**: PASS. Capability gating is explicit and unsupported states fail closed.
- **X. Facilitate Continuous Improvement**: PASS. Fixtures and disabled reasons support debugging.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Traceability artifacts cover all source requirements.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. No canonical docs are converted into migration logs.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. No compatibility aliases or queue fallback paths are introduced.

## Complexity Tracking

No constitution violations or complexity exceptions.
