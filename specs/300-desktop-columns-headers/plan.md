# Implementation Plan: Desktop Columns and Compound Headers

**Branch**: `300-desktop-columns-headers`  
**Date**: 2026-05-05  
**Spec**: `specs/300-desktop-columns-headers/spec.md`  
**Input**: Single-story runtime spec generated from the trusted Jira preset brief for `MM-587`.

## Summary

Tasks List now has the desired task-oriented desktop columns, default scheduled sort, timestamp/string/status sort rules, status pills, dependency summaries, compound header sort/filter targets, header popovers for status/repository/runtime filters, clickable filter chips, and runtime filter propagation through the task-scoped Temporal list request. The implementation updated the existing React entrypoint and CSS, added Vitest coverage for desktop header behavior, and added FastAPI route coverage for the new `targetRuntime` filter.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/tasks-list.tsx` `TABLE_COLUMNS`; UI tests cover columns | no new implementation | UI unit |
| FR-002 | implemented_verified | UI tests assert Kind, Workflow Type, Entry, and Started absent | no new implementation | UI unit |
| FR-003 | implemented_verified | compound header controls in `frontend/src/entrypoints/tasks-list.tsx`; UI test covers separate targets | no new implementation | UI unit |
| FR-004 | implemented_verified | UI test confirms sort label changes sort without opening a filter popover | no new implementation | UI unit |
| FR-005 | implemented_verified | UI test confirms filter target opens matching popover without changing sort | no new implementation | UI unit |
| FR-006 | implemented_verified | `aria-sort` and sort indicator tests pass after compound header refactor | no new implementation | UI unit |
| FR-007 | implemented_verified | `sortRows`, `TIMESTAMP_SORT_FIELDS`, default sort state, and scheduled sort test | no new implementation | UI unit |
| FR-008 | implemented_verified | status, repository, and runtime filters are available through header popovers | no new implementation | UI unit + API route |
| FR-009 | implemented_verified | active filter chips are buttons and reopen matching popovers | no new implementation | UI unit |
| FR-010 | implemented_verified | clear filters includes status, repository, runtime, and pagination reset | no new implementation | UI unit |
| FR-011 | implemented_verified | status pill, links, date formatting, runtime labels, and dependency summary tests pass | no new implementation | UI unit |
| FR-012 | implemented_verified | legacy scope normalization tests pass; runtime filter remains task-scoped | no new implementation | UI unit + API route |
| FR-013 | implemented_verified | MM-587 is preserved in spec, plan, tasks, and verification artifacts | no new implementation | final verify |
| SC-001 | implemented_verified | UI tests cover default and excluded columns | no new implementation | UI unit |
| SC-002 | implemented_verified | UI test covers sort target without popover | no new implementation | UI unit |
| SC-003 | implemented_verified | UI test covers filter target without sort change | no new implementation | UI unit |
| SC-004 | implemented_verified | UI test covers clickable status, repository, and runtime chips | no new implementation | UI unit |
| SC-005 | implemented_verified | API route test confirms `mm_target_runtime` query with task scope | no new implementation | API unit |
| SC-006 | implemented_verified | `verification.md` preserves MM-587 and source design IDs | no new implementation | final verify |
| DESIGN-REQ-006 | implemented_verified | row model and dependency summary tests pass | no new implementation | UI unit |
| DESIGN-REQ-007 | implemented_verified | table sort/format tests pass | no new implementation | UI unit |
| DESIGN-REQ-008 | implemented_verified | column header filters and active chips replace top status/repository filters | no new implementation | UI unit |
| DESIGN-REQ-010 | implemented_verified | desired columns preserved; excluded columns absent | no new implementation | UI unit |
| DESIGN-REQ-011 | implemented_verified | compound controls, sort/filter separation, `aria-sort`, and single-sort behavior covered | no new implementation | UI unit |
| DESIGN-REQ-027 | implemented_verified | no system workflow browsing or multi-sort controls introduced | no new implementation | UI unit |

## Technical Context

- **Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for FastAPI route tests.
- **Primary Dependencies**: React, TanStack Query, Vitest, Testing Library, FastAPI, pytest.
- **Storage**: No new persistent storage.
- **Unit Testing**: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`; API route coverage through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py`.
- **Integration Testing**: UI entrypoint render tests and FastAPI route-boundary test cover the user workflow and API request shape; full repository validation uses `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- **Target Platform**: Browser Mission Control Tasks List and MoonMind API.
- **Project Type**: Existing full-stack web app.
- **Performance Goals**: Header filtering must not add extra fetches except when filter state changes; current-page sorting stays client-side.
- **Constraints**: Preserve task-only visibility, avoid system workflow browsing, keep no multi-column sort controls, and preserve existing row formatting.
- **Scale/Scope**: One Tasks List entrypoint and one API list query filter.

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. Uses existing Mission Control/API surfaces.
- II One-Click Agent Deployment: PASS. No new external services.
- III Avoid Vendor Lock-In: PASS. No vendor-specific behavior.
- IV Own Your Data: PASS. Uses existing MoonMind API data.
- V Skills Are First-Class: PASS. No skill runtime changes.
- VI Replaceable Scaffolding: PASS. Small UI/API boundary change with tests.
- VII Runtime Configurability: PASS. No hardcoded deployment config.
- VIII Modular Architecture: PASS. Scoped to Tasks List UI and executions route filter.
- IX Resilient by Default: PASS. No workflow/activity contract changes.
- X Continuous Improvement: PASS. Verification artifacts capture outcome.
- XI Spec-Driven Development: PASS. Spec, plan, tasks, and verification are created.
- XII Canonical Documentation: PASS. No canonical docs migration notes are added.
- XIII Pre-Release Compatibility: PASS. No compatibility wrapper is introduced; existing legacy URL safety remains.

## Project Structure

```text
frontend/src/entrypoints/tasks-list.tsx
frontend/src/entrypoints/tasks-list.test.tsx
frontend/src/styles/mission-control.css
api_service/api/routers/executions.py
tests/unit/api/routers/test_executions.py
specs/300-desktop-columns-headers/
```

## Complexity Tracking

None.
