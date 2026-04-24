# Implementation Plan: Temporal Editing Hardening

**Branch**: `170-temporal-editing-hardening` | **Date**: 2026-04-13 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/170-temporal-editing-hardening/spec.md`

## Summary

Harden the Temporal-native task editing feature for production rollout. The implementation completes the Phase 5 scope by adding bounded telemetry for edit/rerun entry, draft reconstruction, and submit outcomes; expanding regression coverage for supported and failing edit/rerun flows; removing queue-era primary-flow language and routes from runtime surfaces; and documenting rollout gates for local, staging, dogfood, limited production, and all-operator enablement.

The technical approach is to extend the existing Temporal task editing runtime surfaces rather than create a parallel edit system. Frontend telemetry and regression checks stay near the task-detail and shared task-create entrypoints. Server telemetry stays at the `/api/executions/{workflowId}/update` boundary and uses existing best-effort metrics/logging helpers. Cleanup focuses on primary runtime UI/copy while leaving clearly historical specs alone.

## Technical Context

**Language/Version**: Python 3.12 backend; TypeScript/React frontend
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async models/projections, React, TanStack Query, Zod, Vitest, pytest, shared StatsD metrics emitter
**Storage**: Existing Temporal execution records, canonical projection rows, immutable artifact references, session storage for redirect notices; no new persistence tables expected
**Testing**: `./tools/test_unit.sh`, targeted `pytest`, targeted Vitest, frontend typecheck and lint for edited files
**Target Platform**: MoonMind API service and Mission Control task dashboard served through the existing Docker Compose deployment
**Project Type**: Single repository with backend API, Temporal service, shared schemas, and frontend Mission Control entrypoints
**Performance Goals**: Telemetry is best-effort and constant-time per event; no additional polling loops; no unbounded payloads, artifact content, or task instructions in telemetry
**Constraints**: Runtime mode; production code changes plus validation tests required; `MoonMind.Run` only for current edit/rerun support; capability flags remain authoritative; submit revalidates state; no queue fallback; historical artifacts are immutable; telemetry failures must not block operator flows; primary runtime copy must avoid queue-era terminology
**Scale/Scope**: Phase 5 hardening for one shared submit page, one Temporal detail surface, existing edit/rerun update endpoint, bounded telemetry, regression coverage, cleanup, and rollout readiness

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The feature keeps Temporal execution updates as the edit/rerun substrate and does not introduce a new editing engine.
- **II. One-Click Agent Deployment**: PASS. No new required external services or secrets are introduced; telemetry uses existing best-effort metrics/logging configuration.
- **III. Avoid Vendor Lock-In**: PASS. The work is provider-neutral and applies to MoonMind execution contracts rather than vendor-specific agent behavior.
- **IV. Own Your Data**: PASS. Events, artifacts, and execution state remain under operator-controlled MoonMind infrastructure.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill selection state is preserved as part of the existing draft/payload surfaces and is not redefined.
- **VI. The Bittersweet Lesson**: PASS. The plan adds thin telemetry and tests around existing contracts rather than deep new scaffolding.
- **VII. Powerful Runtime Configurability**: PASS. Rollout continues to use runtime-visible `temporalTaskEditing` configuration.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay within frontend helper/entrypoints, API router telemetry boundary, runtime config, and focused tests.
- **IX. Resilient by Default**: PASS. Submit-side revalidation, stale-state handling, artifact failure handling, and best-effort telemetry preserve operator safety.
- **X. Facilitate Continuous Improvement**: PASS. Telemetry and explicit failure reasons support rollout feedback and future improvement work.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This plan derives from `spec.md` and keeps implementation details in planning artifacts.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Any rollout/backlog notes remain in feature artifacts or `local-only handoffs`, not canonical desired-state docs.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. Queue-era primary runtime references are removed rather than preserved as fallback aliases.

## Project Structure

### Documentation (this feature)

```text
specs/170-temporal-editing-hardening/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│ └── requirements.md
├── contracts/
│ └── temporal-editing-hardening.openapi.yaml
└── tasks.md # Generated later by speckit-tasks
```

### Source Code (repository root)

```text
api_service/
└── api/
 └── routers/
 ├── executions.py
 └── task_dashboard_view_model.py

moonmind/
├── config/
│ └── settings.py
└── schemas/
 └── temporal_models.py

frontend/
└── src/
 ├── entrypoints/
 │ ├── task-create.tsx
 │ ├── task-create.test.tsx
 │ ├── task-detail.tsx
 │ └── task-detail.test.tsx
 └── lib/
 └── temporalTaskEditing.ts

tests/
└── unit/
 └── api/
 └── routers/
 ├── test_executions.py
 └── test_task_dashboard_view_model.py
```

**Structure Decision**: Use existing API, dashboard config, frontend helper, and Mission Control entrypoint boundaries. This avoids a separate telemetry service or task-editing subsystem and keeps the feature close to the contracts it hardens.

## Phase 0: Research Summary

Research is captured in [research.md](./research.md). Decisions:

1. Keep telemetry best-effort and bounded at both client and server boundaries.
2. Use the existing `/api/executions/{workflowId}/update` endpoint as the server telemetry boundary for `UpdateInputs` and `RequestRerun`.
3. Preserve the shared `/tasks/new` edit/rerun form and existing route helper as the mode-resolution authority.
4. Treat queue-era cleanup as primary runtime cleanup, not historical spec rewriting.
5. Validate rollout readiness through automated tests plus explicit quickstart checks rather than adding new persistent rollout state.

## Phase 1: Design Outputs

- [data-model.md](./data-model.md): Defines the telemetry event, failure reason, regression scenario, primary runtime flow, and rollout stage entities.
- [contracts/temporal-editing-hardening.openapi.yaml](./contracts/temporal-editing-hardening.openapi.yaml): Captures the relevant runtime read/update/config contracts and telemetry event shape.
- [quickstart.md](./quickstart.md): Lists deterministic validation commands and manual checks for Phase 5 readiness.

No document requirement identifiers are present in `spec.md`, so `contracts/requirements-traceability.md` is not required for this feature.

## Implementation Strategy

### 1. Client Telemetry

- Add or extend a small frontend helper that emits bounded Temporal task editing events.
- Record detail-page Edit and Rerun click events only when navigation is allowed.
- Record draft reconstruction success and failure in edit and rerun modes.
- Record edit and rerun submit attempts and results, including update name, mode, result, applied outcome, and bounded failure reason.
- Ensure telemetry failures are caught and never block the operator flow.

### 2. Server Telemetry

- Emit best-effort metrics and structured logs from the execution update route for `UpdateInputs` and `RequestRerun`.
- Tag events with bounded update name, workflow type, state, result, failure reason, and applied outcome.
- Avoid raw payloads, task instructions, artifact bodies, credentials, and other unbounded content.
- Preserve existing validation and response semantics.

### 3. Regression Coverage

- Extend frontend coverage for mode resolution, rerun-over-edit precedence, route helpers, payload building, draft reconstruction, artifact read failures, artifact preparation failures, stale-state submit failures, success redirect, and telemetry events.
- Extend backend unit coverage for task editing update telemetry and validation failure telemetry.
- Keep tests hermetic and compatible with `./tools/test_unit.sh`.

### 4. Queue-Era Cleanup

- Search primary runtime UI/helpers for `/tasks/queue/new`, `editJobId`, queue update route usage, and queue resubmit wording.
- Remove or revise operator-facing copy that implies queue-era edit/rerun behavior.
- Do not rewrite historical specs unless they are presented as current primary runtime guidance.

### 5. Rollout Readiness

- Keep `temporalTaskEditing` as the runtime-visible rollout control.
- Validate local and staging enablement paths through dashboard config tests and quickstart checks.
- Define rollout gates around low edit/rerun failure rates, no queue fallback usage, acceptable support feedback, and ability to disable exposure without changing code.

## Post-Design Constitution Re-Check

- **I. Orchestrate, Don't Recreate**: PASS. Design still uses Temporal updates and existing UI surfaces.
- **II. One-Click Agent Deployment**: PASS. No new required service or secret is added.
- **III. Avoid Vendor Lock-In**: PASS. No vendor-specific runtime dependency is introduced.
- **IV. Own Your Data**: PASS. Telemetry remains bounded and local to MoonMind infrastructure.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill state remains data carried through existing draft/payload paths.
- **VI. The Bittersweet Lesson**: PASS. Thin telemetry and tests remain easy to remove or evolve.
- **VII. Powerful Runtime Configurability**: PASS. Existing feature flag remains the rollout lever.
- **VIII. Modular and Extensible Architecture**: PASS. No new cross-cutting subsystem is introduced.
- **IX. Resilient by Default**: PASS. Failure handling remains explicit and telemetry is non-blocking.
- **X. Facilitate Continuous Improvement**: PASS. The plan improves operational visibility for rollout and support.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Plan, research, model, contract, and quickstart artifacts align with the spec.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Rollout sequencing stays in feature artifacts.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. Cleanup removes current primary legacy references instead of adding compatibility aliases.

## Complexity Tracking

No constitution violations or complexity exceptions.
