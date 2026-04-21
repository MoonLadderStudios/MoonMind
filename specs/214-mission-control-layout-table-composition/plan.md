# Implementation Plan: Mission Control Layout and Table Composition Patterns

**Branch**: `run-jira-orchestrate-for-mm-426-standard-7f2da784` | **Date**: 2026-04-21 | **Spec**: `specs/214-mission-control-layout-table-composition/spec.md`  
**Input**: Single-story feature specification from `specs/214-mission-control-layout-table-composition/spec.md`

## Summary

Implement MM-426 by making Mission Control's task-list composition match the desired control-deck plus data-slab pattern from `docs/UI/MissionControlDesignSystem.md`. Repo inspection shows the task list already has functional filters, sorting, pagination, constrained columns, and mobile cards, but its controls and table are one loose stack. The implementation keeps behavior intact while adding semantic control/data surfaces, active-filter chips, sticky table headers, and a tokenized shared `DataTable` slab for existing dense-table consumers.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | task list has title, filters, and live updates but no named control deck | group them in `.task-list-control-deck.panel--controls` | UI unit |
| FR-002 | partial | result toolbar and table exist in `.queue-layouts` | make `.task-list-data-slab.panel--data` the connected result surface | UI unit |
| FR-003 | missing | filters update query/request state but active chips are absent | add active filter chips and clear action | UI unit |
| FR-004 | partial | table has constrained columns but headers are not sticky | make table wrapper scrollable and headers sticky | UI unit / CSS |
| FR-005 | implemented_unverified | existing constrained column tests cover long workflow IDs | preserve and rerun tests | UI unit |
| FR-006 | partial | `DataTable` uses standalone Tailwind utility classes | switch to shared Mission Control table classes | compile/UI unit |
| FR-007 | implemented_unverified | existing task-list tests cover behavior | rerun focused and wrapper tests | UI unit |
| FR-008 | missing | no MM-426-specific tests | add composition and filter-chip tests | UI unit |
| FR-009 | implemented_unverified | `spec.md` preserves MM-426 and source summary | preserve through tasks, verification, and commit | final verify |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; CSS for shared Mission Control styling  
**Primary Dependencies**: React, TanStack Query, Vite/Vitest, existing Mission Control shared stylesheet  
**Storage**: No new persistent storage  
**Unit Testing**: `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx`; direct `./node_modules/.bin/vitest` if the npm script cannot resolve `vitest` in the managed container; final wrapper via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`  
**Integration Testing**: Existing task-list UI test render exercises API request shape and routing links; no compose-backed integration is required because backend contracts are unchanged  
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

**Structure Decision**: Keep behavior in the existing task-list entrypoint and shared table component. Use CSS classes as the layout contract so other Mission Control table consumers can adopt the same slab posture without new runtime APIs.

## Complexity Tracking

No constitution violations.
