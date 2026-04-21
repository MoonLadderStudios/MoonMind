# Implementation Plan: Mission Control Layout and Table Composition Patterns

**Branch**: `run-jira-orchestrate-for-mm-426-standard-7f2da784` | **Date**: 2026-04-21 | **Spec**: `specs/214-mission-control-layout-table-composition/spec.md`  
**Input**: Single-story feature specification from `specs/214-mission-control-layout-table-composition/spec.md`

## Summary

Implement MM-426 by making Mission Control's task-list composition match the desired control-deck plus data-slab pattern from `docs/UI/MissionControlDesignSystem.md`. Current repo inspection shows the implementation is present: task-list markup exposes semantic control/data surfaces, active-filter chips, sticky table headers, and shared `DataTable` slab classes while preserving existing request, sorting, pagination, and mobile-card behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/tasks-list.tsx` renders `.task-list-control-deck.panel--controls`; `tasks-list.test.tsx` asserts the control deck exists. | no new implementation | UI unit |
| FR-002 | implemented_verified | `tasks-list.tsx` renders `.task-list-data-slab.panel--data`; `tasks-list.test.tsx` asserts the data slab and table wrapper exist. | no new implementation | UI unit |
| FR-003 | implemented_verified | `tasks-list.tsx` renders `.task-list-filter-chip` entries and `Clear filters`; `tasks-list.test.tsx` covers chip rendering and clearing. | no new implementation | UI unit |
| FR-004 | implemented_verified | `mission-control.css` sets `.queue-table-wrapper th { position: sticky; }`; `tasks-list.test.tsx` verifies computed sticky positioning. | no new implementation | UI unit / CSS |
| FR-005 | implemented_verified | Existing task-list tests cover long workflow IDs, and `.queue-table-wrapper` / cell styles constrain dense table layout. | no new implementation | UI unit |
| FR-006 | implemented_verified | `frontend/src/components/tables/DataTable.tsx` emits `.data-table-slab`, `.data-table`, and `.data-table-empty` classes. | no new implementation | compile/UI unit |
| FR-007 | implemented_verified | Existing task-list tests continue to cover request behavior, sorting, pagination, dependency summaries, runtime labels, and mobile cards. | no new implementation | UI unit |
| FR-008 | implemented_verified | `tasks-list.test.tsx` includes MM-426-focused composition, filter-chip, and sticky-header assertions. | no new implementation | UI unit |
| FR-009 | implemented_verified | `spec.md`, `verification.md`, and `docs/tmp/jira-orchestration-inputs/MM-426-moonspec-orchestration-input.md` preserve MM-426, the trusted Jira preset brief, and source design coverage IDs. | no new implementation | final verify |
| DESIGN-REQ-012 | implemented_verified | `spec.md` maps MM-426 to the Mission Control layout system; `tasks-list.tsx` uses control and data surfaces that support data-wide route composition. | no new implementation | UI unit |
| DESIGN-REQ-013 | implemented_verified | `frontend/src/styles/mission-control.css` uses a three-zone masthead grid, and `frontend/src/entrypoints/mission-control.test.tsx` verifies brand-left, nav-centered, version-right desktop alignment. | no new implementation | UI unit |
| DESIGN-REQ-014 | implemented_verified | Control deck/filter cluster requirements are implemented by `.task-list-control-deck`, `.task-list-control-grid`, utility cluster, chips, and clear action. | no new implementation | UI unit |
| DESIGN-REQ-019 | implemented_verified | `/tasks/list` uses the control deck + data slab structure, sticky table header, attached pagination/page-size controls, and table-first desktop posture. | no new implementation | UI unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; CSS for shared Mission Control styling  
**Primary Dependencies**: React, TanStack Query, Vite/Vitest, existing Mission Control shared stylesheet  
**Storage**: No new persistent storage  
**Unit Testing**: `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/mission-control.test.tsx`; direct `./node_modules/.bin/vitest` if the npm script cannot resolve `vitest` in the managed container; final wrapper via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`
**Integration Testing**: Existing task-list UI render tests exercise API request shape, route links, and user-level filter/pagination flows in an integration-style browser component boundary; no compose-backed integration is required because backend contracts are unchanged
**Target Platform**: Browser-hosted Mission Control UI served by FastAPI  
**Project Type**: Web UI composition/design-system story  
**Performance Goals**: Avoid new network calls, runtime loops, or heavy visual effects; sticky headers are CSS-only  
**Constraints**: Preserve task-list request parameters, sorting, pagination, mobile card behavior, and route ownership  
**Scale/Scope**: Task list entrypoint, shared DataTable component, shared Mission Control stylesheet, focused UI tests, MoonSpec artifacts

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The story uses the existing Jira Orchestrate/MoonSpec lifecycle and reuses current UI entrypoints.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS. No provider-specific dependency is introduced.
- IV. Own Your Data: PASS. No external data movement or browser-direct provider calls.
- V. Skills Are First-Class and Easy to Add: PASS. Skill runtime and materialization paths are untouched.
- VI. Replaceable Scaffolding: PASS. The work is small, CSS/markup-oriented, and covered by focused tests.
- VII. Runtime Configurability: PASS. Existing runtime config behavior remains unchanged.
- VIII. Modular Architecture: PASS. Changes stay in Mission Control UI components and stylesheet.
- IX. Resilient by Default: PASS. No workflow or side-effect contract changes; existing behavior tests continue to run.
- X. Continuous Improvement: PASS. Verification evidence is captured in MoonSpec artifacts.
- XI. Spec-Driven Development: PASS. Implementation proceeds from this single-story spec.
- XII. Documentation Separation: PASS. Desired-state docs remain canonical; runtime traceability input stays under `docs/tmp`.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases or hidden fallback contract is introduced.

## Project Structure

```text
specs/214-mission-control-layout-table-composition/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── contracts/
│   └── layout-table-composition.md
├── tasks.md
└── verification.md

frontend/src/
├── components/tables/DataTable.tsx
├── entrypoints/tasks-list.tsx
├── entrypoints/tasks-list.test.tsx
└── styles/mission-control.css

docs/tmp/jira-orchestration-inputs/
└── MM-426-moonspec-orchestration-input.md
```

`data-model.md` is intentionally absent because MM-426 introduces no persisted entity, state machine, schema, or data contract.

**Structure Decision**: Keep behavior in the existing task-list entrypoint and shared table component. Use CSS classes as the layout contract so other Mission Control table consumers can adopt the same slab posture without new runtime APIs.

## Complexity Tracking

No constitution violations.
