# Implementation Plan: Column Filter Popovers, Chips, and Selection Semantics

**Branch**: `301-column-filter-popovers`
**Date**: 2026-05-05
**Spec**: `specs/301-column-filter-popovers/spec.md`
**Input**: Single-story runtime spec generated from the trusted Jira preset briefs for `MM-588` and `MM-594`.

## Summary

Tasks List already has the `MM-587` desktop column/header foundation and the completed `MM-588` column filter implementation: task-focused columns, separated sort/filter header targets, column filter popovers with staged edits, include/exclude modes, blank handling, Skill and date filters, removable chips, pagination reset, canonical URL/API encoding, and task-scope API safety. MM-594 maps to this same single story and adds traceability for the requirement that filter buttons open a box/popover with multi-select options instead of standard single-select dropdowns. No new implementation work is planned unless refreshed verification exposes a regression; verification remains UI-first with Vitest/Testing Library coverage for interaction semantics and Python route-boundary coverage for canonical task-scoped query filters.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `verification.md` maps filter controls to `frontend/src/entrypoints/tasks-list.tsx` and `tasks-list.test.tsx` | no new implementation; preserve behavior | UI unit final verify |
| FR-002 | implemented_verified | `verification.md` confirms old top detached filters are replaced by column/mobile equivalents | no new implementation; preserve behavior | UI unit final verify |
| FR-003 | implemented_verified | staged apply tests in `tasks-list.test.tsx` are cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| FR-004 | implemented_verified | Cancel/Escape/outside-click tests in `tasks-list.test.tsx` are cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| FR-005 | implemented_verified | status popover implementation/tests verify select-all/no-filter value-list behavior | no new implementation; preserve behavior | UI unit final verify |
| FR-006 | implemented_verified | UI and route tests verify include arrays for value filters | no new implementation; preserve behavior | UI unit + route-boundary final verify |
| FR-007 | implemented_verified | UI and route tests verify exclude filters and `Status: not canceled` | no new implementation; preserve behavior | UI unit + route-boundary final verify |
| FR-008 | implemented_verified | lifecycle status order and route query tests are cited by `verification.md` | no new implementation; preserve behavior | UI unit + route-boundary final verify |
| FR-009 | implemented_verified | runtime tests verify raw stored values with readable labels | no new implementation; preserve behavior | UI unit final verify |
| FR-010 | implemented_verified | skill filter UI/API evidence is cited by `verification.md` | no new implementation; preserve behavior | UI unit + route-boundary final verify |
| FR-011 | implemented_verified | repository value and exact text behavior are covered by UI/API tests | no new implementation; preserve behavior | UI unit + route-boundary final verify |
| FR-012 | implemented_verified | Scheduled/Finished date bounds and blank behavior are covered by UI/API tests | no new implementation; preserve behavior | UI unit + route-boundary final verify |
| FR-013 | implemented_verified | Created date bounds without blank filtering are covered by UI/API tests | no new implementation; preserve behavior | UI unit + route-boundary final verify |
| FR-014 | implemented_verified | bounded option derivation is cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| FR-015 | implemented_verified | React text rendering and malicious-label coverage are cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| FR-016 | implemented_verified | active chip tests cover all active column filter summaries | no new implementation; preserve behavior | UI unit final verify |
| FR-017 | implemented_verified | chip-open tests prove matching popovers reopen with applied state | no new implementation; preserve behavior | UI unit final verify |
| FR-018 | implemented_verified | chip removal tests prove only the selected filter clears | no new implementation; preserve behavior | UI unit final verify |
| FR-019 | implemented_verified | Clear filters behavior is covered by active chip tests | no new implementation; preserve behavior | UI unit final verify |
| FR-020 | implemented_verified | pagination-reset assertions are cited by `verification.md` | no new implementation; preserve behavior | UI unit + route-boundary final verify |
| FR-021 | implemented_verified | legacy `state=<value>` load mapping tests are cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| FR-022 | implemented_verified | legacy `repo=<value>` load mapping tests are cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| FR-023 | implemented_verified | canonical URL rewrite tests are cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| FR-024 | implemented_verified | API scope/query tests verify task-scoped semantics | no new implementation; preserve behavior | route-boundary final verify |
| FR-025 | implemented_verified | `spec.md` now preserves `MM-588` and `MM-594`; this plan carries both | no new implementation; preserve both keys downstream | final verify |
| SC-001 | implemented_verified | staged apply tests are cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| SC-002 | implemented_verified | cancel/Escape/outside-click tests are cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| SC-003 | implemented_verified | exclude status chip tests and route assertions are cited by `verification.md` | no new implementation; preserve behavior | UI unit + route-boundary final verify |
| SC-004 | implemented_verified | runtime and skill display tests are cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| SC-005 | implemented_verified | repository selection and exact mapping tests are cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| SC-006 | implemented_verified | chip reopen, individual removal, and clear-all tests are cited by `verification.md` | no new implementation; preserve behavior | UI unit final verify |
| SC-007 | implemented_verified | API route tests verify canonical filters remain task-scoped | no new implementation; preserve behavior | route-boundary final verify |
| SC-008 | implemented_verified | `spec.md` and this plan preserve `MM-588`, `MM-594`, and source design IDs | no new implementation; preserve both keys downstream | final verify |
| DESIGN-REQ-007 | implemented_verified | `verification.md` maps layout/filter/chip behavior to implementation and tests | no new implementation; preserve behavior | UI unit final verify |
| DESIGN-REQ-012 | implemented_verified | `verification.md` maps staged popover behavior to implementation and tests | no new implementation; preserve behavior | UI unit final verify |
| DESIGN-REQ-013 | implemented_verified | include/exclude semantics are covered by UI and route tests | no new implementation; preserve behavior | UI unit + route-boundary final verify |
| DESIGN-REQ-014 | implemented_verified | active chip behavior is covered by UI tests | no new implementation; preserve behavior | UI unit final verify |
| DESIGN-REQ-015 | implemented_verified | field-specific status/runtime/repository/date behavior is covered by UI and route tests | no new implementation; preserve behavior | UI unit + route-boundary final verify |
| DESIGN-REQ-027 | implemented_verified | task-scope route tests preserve non-goal safety | no new implementation; preserve behavior | route-boundary final verify |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for FastAPI route tests.
**Primary Dependencies**: React, TanStack Query, Zod, Vitest, Testing Library, FastAPI, pytest, Temporal visibility query helpers.
**Storage**: No new persistent storage; filter state is URL/query state and component state only.
**Unit Testing**: Focused UI unit coverage runs with `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`. Focused Python route-boundary unit coverage runs with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py`. Full required unit verification runs with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
**Integration Testing**: No compose-backed `integration_ci` coverage is required for this UI and route-planning refresh because no service topology, artifact store, worker routing, or external dependency contract changes are planned. The integration strategy is route-boundary validation for `/api/executions` task-scope query construction plus UI entrypoint workflow tests, both running without external credentials.
**Target Platform**: Browser Mission Control Tasks List and MoonMind API.
**Project Type**: Existing full-stack web app.
**Performance Goals**: Popover interactions must not fetch until Apply/remove/clear; value lists remain bounded; no unbounded DOM rendering for long lists.
**Constraints**: Preserve task-only visibility, avoid raw Temporal query authoring, keep direct browser calls to MoonMind APIs only, keep page size/pagination outside filters, and preserve `MM-588` and `MM-594` traceability.
**Scale/Scope**: One Tasks List entrypoint, shared Mission Control CSS, and one API list route filter surface.

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. Extends existing Mission Control/API surfaces.
- II One-Click Agent Deployment: PASS. No new external services or mandatory secrets.
- III Avoid Vendor Lock-In: PASS. Uses MoonMind task metadata and Temporal-facing adapter boundary already present.
- IV Own Your Data: PASS. Uses locally served MoonMind task data and URL state.
- V Skills Are First-Class: PASS. No skill runtime changes.
- VI Replaceable Scaffolding: PASS. UI/API behavior is bounded by tests and compact contracts.
- VII Runtime Configurability: PASS. No hardcoded deployment configuration.
- VIII Modular Architecture: PASS. Scope remains Tasks List UI plus list route query handling.
- IX Resilient by Default: PASS. No workflow/activity payload changes; route behavior remains fail-safe for legacy workflow scope.
- X Continuous Improvement: PASS. MoonSpec artifacts and verification will capture evidence.
- XI Spec-Driven Development: PASS. Work starts from `spec.md`, then plan/tasks/verify.
- XII Canonical Documentation: PASS. No migration notes are added to canonical docs.
- XIII Pre-Release Compatibility: PASS. Internal query shape can evolve; legacy URL meanings are preserved as explicit load-time mappings.

## Project Structure

### Documentation (this feature)

```text
specs/301-column-filter-popovers/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── tasks-list-column-filter-popovers.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/tasks-list.tsx
frontend/src/entrypoints/tasks-list.test.tsx
frontend/src/styles/mission-control.css
api_service/api/routers/executions.py
tests/unit/api/routers/test_executions.py
```

**Structure Decision**: Use the existing Mission Control Tasks List entrypoint and route-boundary tests. No new package, page, persistent table, or canonical docs file is required.

## Complexity Tracking

None.
