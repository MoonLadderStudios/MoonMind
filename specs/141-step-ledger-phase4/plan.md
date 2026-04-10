# Implementation Plan: Step Ledger Phase 4

**Branch**: `141-step-ledger-phase4` | **Date**: 2026-04-09 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/141-step-ledger-phase4/spec.md`

## Summary

Pivot Mission Control task detail to a Steps-first experience backed by the existing `/api/executions/{workflowId}/steps` contract. The frontend will fetch execution detail first, then the latest-run step ledger, render the step ledger above Timeline and generic Artifacts, and lazily attach step-scoped observability and artifact evidence only when a row expands.

## Technical Context

**Language/Version**: TypeScript + React 18, Vitest, existing Mission Control CSS tokens  
**Primary Dependencies**: TanStack Query, Zod, generated OpenAPI shapes already checked in, existing `/api/executions` and `/api/task-runs` APIs, dashboard runtime config route templates from FastAPI  
**Storage**: Browser query cache only; latest-run step state continues to come from workflow-backed API reads and task-run observability endpoints  
**Testing**: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, `npm run ui:typecheck`, `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`  
**Target Platform**: Mission Control React/Vite frontend served by FastAPI  
**Project Type**: Frontend task-detail UI plus backend runtime-config support and browser-test coverage  
**Performance Goals**: Initial task detail remains bounded; step observability fetches only occur for expanded rows that expose `taskRunId`; expanded-row state survives ordinary polling  
**Constraints**: Preserve latest-run-only semantics; reuse existing `/api/task-runs/*` observability routes instead of inventing new browser contracts; keep the generic Artifacts panel secondary; keep existing Mission Control semantic classes and tokenized styling  
**Scale/Scope**: Task-detail page, related CSS, browser tests, and dashboard runtime-config route-template delivery

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The UI consumes the workflow-owned/API-owned step ledger directly instead of inventing a parallel client state model.
- **IV. Own Your Data**: PASS. Logs and diagnostics stay on artifact-backed observability routes; the UI links to them per step.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The work reuses the frozen step-ledger contract and Phase 3 API routes unchanged.
- **IX. Resilient by Default**: PASS. Latest-run reads stay keyed by `workflowId`, expanded step panels degrade safely when `taskRunId` is absent or observability fetches fail, and browser tests cover delayed binding.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Phase 4 has a dedicated feature package with explicit acceptance and validation gates.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical semantics remain in the UI and step-ledger docs; this plan captures only the rollout work.
- **XIII. Pre-Release Delete, Don't Deprecate**: PASS. The page pivots to the canonical Steps surface instead of preserving the old observability-first model as an equal primary view.

## Project Structure

### Documentation (this feature)

```text
specs/141-step-ledger-phase4/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── task-detail-steps-ui.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-detail.tsx        # MODIFY: fetch/render the Steps-first task-detail experience
frontend/src/entrypoints/task-detail.test.tsx   # MODIFY: browser tests for Steps-first layout and row-level observability
frontend/src/styles/mission-control.css         # MODIFY: dense step rows, status chips, check badges, expanded evidence groups
api_service/api/routers/task_dashboard_view_model.py   # MODIFY: expose task-run route templates to the task-detail runtime config
tests/unit/api/routers/test_task_dashboard_view_model.py   # MODIFY: cover runtime-config route-template delivery
```

**Structure Decision**: Keep the task-detail page as the only owner of the new Steps-first composition. Do not create a new page route or alternate detail entrypoint; instead, factor row-level helpers/components inside `task-detail.tsx` as needed and keep styling in the shared Mission Control stylesheet.

## Complexity Tracking

The main risk is stale or noisy polling causing expanded rows to collapse or observability fetches to fire too aggressively. Mitigation: separate execution-detail and step-ledger queries, key row-level observability on the row `taskRunId`, and preserve expansion state by logical step id instead of array position alone.
