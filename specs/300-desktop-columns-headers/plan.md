# Implementation Plan: Desktop Columns and Compound Headers

**Branch**: `300-desktop-columns-headers`  
**Date**: 2026-05-05  
**Spec**: `specs/300-desktop-columns-headers/spec.md`  
**Input**: Single-story runtime spec generated from the trusted Jira preset brief for `MM-587`.

## Summary

Tasks List already has the desired task-oriented desktop columns, default scheduled sort, timestamp/string/status sort rules, status pills, and dependency summaries. The missing behavior is the compound header interaction model: separate sort and filter targets in every visible header, header popovers for status/repository/runtime filters, clickable filter chips, and runtime filter propagation through the task-scoped Temporal list request. Implementation will update the existing React entrypoint and its CSS, add Vitest coverage for desktop header behavior, and add FastAPI route coverage for the new `targetRuntime` filter.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/tasks-list.tsx` `TABLE_COLUMNS`; existing tests cover columns | preserve | UI unit |
| FR-002 | implemented_verified | existing tests assert Kind, Workflow Type, Entry, Started absent | preserve | UI unit |
| FR-003 | missing | current header is one sort button only | add reusable compound header | UI unit |
| FR-004 | implemented_unverified | existing sort tests cover label sorting but not popover separation | extend tests around compound headers | UI unit |
| FR-005 | missing | no header filter target exists | add filter buttons/popovers | UI unit |
| FR-006 | implemented_unverified | existing `aria-sort` tests cover sort buttons | preserve through compound header refactor | UI unit |
| FR-007 | implemented_verified | `sortRows`, `TIMESTAMP_SORT_FIELDS`, default sort state, existing scheduled sort test | preserve | UI unit |
| FR-008 | partial | status/repository filters exist in top controls; runtime filter absent | move filters to header popovers and add runtime filter | UI unit + API route |
| FR-009 | partial | chips render but are not clickable | make chips buttons that open matching filter | UI unit |
| FR-010 | partial | clear filters handles status/repository only | include runtime and pagination reset | UI unit |
| FR-011 | implemented_verified | status pill, links, date formatting, dependency summary tests exist | preserve | UI unit |
| FR-012 | implemented_verified | existing scope normalization tests and task-scoped request | preserve while adding runtime filter | UI unit + API route |
| FR-013 | missing | new MoonSpec artifacts required | preserve MM-587 in artifacts and verification | final verify |
| SC-001 | implemented_unverified | existing tests cover most columns | keep explicit coverage | UI unit |
| SC-002 | missing | no filter popover to guard against | add regression test | UI unit |
| SC-003 | missing | no filter popover target exists | add regression test | UI unit |
| SC-004 | partial | chips are display-only | add clickable chip tests | UI unit |
| SC-005 | missing | API route lacks `targetRuntime` query filter | add API test and route filter | API unit |
| SC-006 | missing | new artifacts needed | final verification report | final verify |
| DESIGN-REQ-006 | implemented_verified | row model and dependency summary tests | preserve | UI unit |
| DESIGN-REQ-007 | implemented_verified | table sort/format tests | preserve | UI unit |
| DESIGN-REQ-008 | partial | top filters still present | move status/repository to header and add runtime | UI unit |
| DESIGN-REQ-010 | implemented_verified | no excluded columns | preserve | UI unit |
| DESIGN-REQ-011 | missing | compound controls absent | add reusable control | UI unit |
| DESIGN-REQ-027 | implemented_verified | no system workflow browsing or multi-sort controls | preserve | UI unit |

## Technical Context

- **Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for FastAPI route tests.
- **Primary Dependencies**: React, TanStack Query, Vitest, Testing Library, FastAPI, pytest.
- **Storage**: No new persistent storage.
- **Unit Testing**: `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx`; API route coverage through pytest.
- **Integration Testing**: UI entrypoint render tests and FastAPI route-boundary test cover the user workflow and API request shape.
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
