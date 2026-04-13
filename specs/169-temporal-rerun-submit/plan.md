# Implementation Plan: Temporal Rerun Submit

**Branch**: `169-temporal-rerun-submit` | **Date**: 2026-04-13 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `specs/169-temporal-rerun-submit/spec.md`

## Summary

Enable terminal `MoonMind.Run` executions to be rerun from the shared `/tasks/new` Temporal task form. The implementation will reuse existing Temporal draft reconstruction and artifact preparation, then submit a distinct `RequestRerun` execution update instead of falling through to create, queue, or active edit behavior. Success handling returns operators to the Temporal detail context and surfaces latest-run lineage where available; rejection handling remains explicit and non-redirecting.

## Technical Context

**Language/Version**: TypeScript with React/Vite for Mission Control; Python 3.12 for FastAPI and Temporal service code  
**Primary Dependencies**: React, TanStack Query, Vite/Vitest, FastAPI, Pydantic, SQLAlchemy, Temporal Python SDK  
**Storage**: PostgreSQL-backed execution records; artifact storage exposed through MoonMind artifact APIs  
**Testing**: `./tools/test_unit.sh`, pytest, Vitest, TypeScript compiler, ESLint  
**Target Platform**: Docker Compose local-first MoonMind deployment with browser-based Mission Control  
**Project Type**: Web application with FastAPI backend, Temporal workflow/service layer, and Mission Control frontend  
**Performance Goals**: Rerun submission should add no extra page load beyond the existing execution detail/artifact reads and should submit with one execution update request plus artifact writes only when needed  
**Constraints**: Preserve immutable historical artifacts; preserve `UpdateInputs` vs `RequestRerun` semantics; no queue fallback; respect `temporalTaskEditing` feature flag and backend capability flags  
**Scale/Scope**: Initial support is terminal `MoonMind.Run` executions in the shared task form; non-run workflow types, proposal editing, recurring schedule editing, and queue-era flows remain out of scope

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | PASS | Uses existing Temporal execution update contracts rather than rebuilding agent behavior. |
| II. One-Click Agent Deployment | PASS | No new external service or required secret is introduced. |
| III. Avoid Vendor Lock-In | PASS | Rerun logic is workflow/execution-centric and not tied to a model provider. |
| IV. Own Your Data | PASS | New rerun inputs are stored as operator-controlled artifacts when externalized. |
| V. Skills Are First-Class and Easy to Add | PASS | Preserves reconstructed skill/template fields without changing skill runtime semantics. |
| VI. Replaceable AI Scaffolds | PASS | Keeps thin form/update plumbing behind existing contracts with regression tests. |
| VII. Runtime Configurability | PASS | Existing `temporalTaskEditing` and capability flags remain the rollout gates. |
| VIII. Modular and Extensible Architecture | PASS | Changes stay within frontend task form helpers, execution API contract, and Temporal service boundaries. |
| IX. Resilient by Default | PASS | Explicit rejection paths cover stale state, missing artifacts, and backend validation failures. |
| X. Facilitate Continuous Improvement | PASS | Success/failure outcomes remain operator-visible in Mission Control. |
| XI. Spec-Driven Development | PASS | This feature has spec, plan, and generated design artifacts before task generation. |
| XII. Canonical Documentation Separation | PASS | No canonical docs are converted into migration checklists. |
| XIII. Pre-Release Velocity | PASS | No compatibility shim or queue fallback is introduced. |
| Product/Operational Constraints | PASS | Secret hygiene is unaffected; observability remains through execution detail and artifacts. |

## Project Structure

### Documentation (this feature)

```text
specs/169-temporal-rerun-submit/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── temporal-rerun-submit.openapi.yaml
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/
├── entrypoints/
│   ├── task-create.tsx
│   └── task-create.test.tsx
└── lib/
    └── temporalTaskEditing.ts

api_service/api/routers/
└── executions.py

moonmind/
├── schemas/
│   └── temporal_models.py
└── workflows/temporal/
    └── service.py

tests/
├── unit/api/routers/
│   └── test_executions.py
└── unit/workflows/temporal/
    └── test_temporal_service.py
```

**Structure Decision**: This is a cross-layer web feature. The primary implementation is in the Mission Control task creation entrypoint and shared Temporal task editing helper, with backend/service verification only where the existing `RequestRerun` contract or terminal rerun semantics need adjustment.

## Phase 0: Research

Research will resolve implementation decisions for:

- Whether rerun submission can reuse the edit payload builder without weakening semantic separation.
- How artifact-backed rerun inputs should create replacement artifact references.
- How the success path should handle latest-run/continue-as-new responses.
- Which tests must compare edit and rerun behavior.

**Output**: [research.md](./research.md)

## Phase 1: Design & Contracts

Design artifacts define:

- Rerun request, rerun draft, input artifact reference, and lineage data.
- The execution update request/response contract for `RequestRerun`.
- A quickstart validation path that exercises the operator flow and automated tests.

**Outputs**: [data-model.md](./data-model.md), [contracts/temporal-rerun-submit.openapi.yaml](./contracts/temporal-rerun-submit.openapi.yaml), [quickstart.md](./quickstart.md)

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | PASS | Contract uses existing Temporal execution update semantics. |
| II. One-Click Agent Deployment | PASS | Design requires no new deployment dependency. |
| III. Avoid Vendor Lock-In | PASS | Data model is provider-neutral. |
| IV. Own Your Data | PASS | Artifact immutability and replacement references are explicit. |
| V. Skills Are First-Class and Easy to Add | PASS | Skill fields remain part of reconstructed task inputs. |
| VI. Replaceable AI Scaffolds | PASS | Plan keeps narrow contracts and focused tests. |
| VII. Runtime Configurability | PASS | Feature and capability gates are preserved. |
| VIII. Modular and Extensible Architecture | PASS | Frontend, API, and service boundaries stay explicit. |
| IX. Resilient by Default | PASS | Stale state and artifact failure paths are planned and testable. |
| X. Facilitate Continuous Improvement | PASS | Operator-visible outcomes and lineage support troubleshooting. |
| XI. Spec-Driven Development | PASS | Spec and design artifacts are complete for task generation. |
| XII. Canonical Documentation Separation | PASS | Migration details remain in feature artifacts. |
| XIII. Pre-Release Velocity | PASS | Queue fallback remains forbidden instead of preserved. |
| Product/Operational Constraints | PASS | No secret exposure or raw credential handling is added. |

## Complexity Tracking

No constitution violations are introduced. No complexity exceptions are required.
