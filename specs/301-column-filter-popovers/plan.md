# Implementation Plan: Column Filter Popovers, Chips, and Selection Semantics

**Branch**: `301-column-filter-popovers`
**Date**: 2026-05-05
**Spec**: `specs/301-column-filter-popovers/spec.md`
**Input**: Single-story runtime spec generated from the trusted Jira preset brief for `MM-588`.

## Summary

Tasks List already has the `MM-587` desktop column/header foundation: task-focused columns, separated sort/filter header targets, simple status/repository/runtime filtering, active chips, and task-scope API safety. This story will extend that surface into real column filter popovers with staged edits, include/exclude modes, blank handling, Skill and date filters, removable chips, pagination reset, and canonical filter URL/API encoding. Verification will be UI-first with Vitest/Testing Library coverage for interaction semantics and FastAPI route-boundary coverage for canonical task-scoped query filters.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `frontend/src/entrypoints/tasks-list.tsx` has status/repository/runtime only | add Skill, Scheduled, Created, Finished filter controls | UI unit |
| FR-002 | implemented_verified | top Status/Repository controls are absent; tests assert control deck has no old filter form | preserve behavior | UI unit |
| FR-003 | missing | current select/input changes apply immediately | add staged draft state per popover and Apply | UI unit |
| FR-004 | missing | popovers do not implement cancel/Escape/outside dismissal semantics | add non-applying dismissal paths | UI unit |
| FR-005 | partial | empty select value approximates all state for simple filters | add Select all/no-filter model to value-list popovers | UI unit |
| FR-006 | partial | current filters support one included status/repo/runtime value | add include arrays for value filters | UI unit + API route |
| FR-007 | missing | no exclude filter state exists | add exclude arrays and status `not canceled` behavior | UI unit + API route |
| FR-008 | partial | status options use canonical list, but query only supports one exact state | add lifecycle include/exclude mapping using row display precedence where possible | UI unit + API route |
| FR-009 | implemented_unverified | runtime select stores raw value and displays `formatRuntimeLabel` | keep and extend to multi-value chips | UI unit |
| FR-010 | missing | Skill filter has no active control | add Skill value-list filtering | UI unit + API route if supported |
| FR-011 | partial | repository exact text behavior exists; value-list selection does not | add repository value selection while preserving exact text compatibility | UI unit + API route |
| FR-012 | missing | date popovers are placeholders | add Scheduled/Finished bounds and blank handling | UI unit |
| FR-013 | missing | Created date popover is placeholder | add Created bounds without blank filter | UI unit |
| FR-014 | implemented_unverified | current runtime option list is bounded; dynamic long lists not yet needed | keep bounded value lists; document/server-search contingency | UI unit |
| FR-015 | implemented_unverified | React text rendering escapes labels | keep labels as text; test malicious label rendering | UI unit |
| FR-016 | partial | chips exist for three simple filters only | add chips for all active column filter types and modes | UI unit |
| FR-017 | implemented_unverified | current chips reopen status/repository/runtime popovers | extend to all filters | UI unit |
| FR-018 | missing | chips have no separate remove action | add remove button/action per chip | UI unit |
| FR-019 | partial | Clear filters clears status/repo/runtime only | clear all new column filters and restore default task-run view | UI unit |
| FR-020 | partial | existing changes reset pagination immediately | ensure Apply/remove/clear reset cursor pagination | UI unit |
| FR-021 | implemented_verified | tests cover legacy `state=<value>` loading | preserve as Status include filter | UI unit |
| FR-022 | implemented_verified | tests cover legacy `repo=<value>` loading | preserve as Repository exact include filter | UI unit |
| FR-023 | partial | URL still writes legacy `state`, `repo`, `targetRuntime` names | add canonical filter encoding after new UI changes | UI unit |
| FR-024 | implemented_verified | UI/API force `scope=tasks` and normalize unsafe workflow scope state | preserve task-scoped query | UI unit + API route |
| FR-025 | partial | `spec.md` preserves MM-588 | carry MM-588 through plan/tasks/verification | final verify |
| SC-001 | missing | no staged-edit test exists | add interaction test | UI unit |
| SC-002 | missing | no cancel/Escape/outside test exists | add interaction test | UI unit |
| SC-003 | missing | no exclude status chip exists | add exclude-mode test | UI unit + API route |
| SC-004 | implemented_unverified | runtime display formatting test exists for simple select | extend to multi-value popover/chip | UI unit |
| SC-005 | partial | legacy exact repo test exists | add repository value selection and exact mapping tests | UI unit |
| SC-006 | partial | chip reopen and clear-all tests exist, no individual remove | add chip remove tests | UI unit |
| SC-007 | partial | page reset happens on immediate changes | verify Apply/remove/clear reset pagination with task scope | UI unit + API route |
| SC-008 | partial | MM-588 preserved in spec and plan | preserve through tasks and verification | final verify |
| DESIGN-REQ-007 | partial | prior story covers header migration and chips for three filters | add all MM-588 filter semantics | UI unit |
| DESIGN-REQ-012 | missing | current popovers are immediate simple controls | add staged, keyboard-accessible popover semantics | UI unit |
| DESIGN-REQ-013 | missing | no include/exclude model | add AND-across-columns, OR-within-column state model | UI unit + API route |
| DESIGN-REQ-014 | partial | current chips reopen filters, clear-all exists | add per-chip removal and all filter modes | UI unit |
| DESIGN-REQ-015 | partial | status/runtime/repo simple behavior exists | add skill/date/blank/legacy/canonical behavior | UI unit + API route |
| DESIGN-REQ-027 | implemented_verified | no raw Temporal query, direct Temporal call, or system browsing in current normal page | preserve non-goals | UI unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for FastAPI route tests.
**Primary Dependencies**: React, TanStack Query, Zod, Vitest, Testing Library, FastAPI, pytest, Temporal visibility query helpers.
**Storage**: No new persistent storage; filter state is URL/query state and component state only.
**Unit Testing**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` for focused UI coverage; targeted Python route tests through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py`.
**Integration Testing**: Existing route-boundary tests for `/api/executions` plus UI entrypoint tests cover the task-list workflow without external credentials.
**Target Platform**: Browser Mission Control Tasks List and MoonMind API.
**Project Type**: Existing full-stack web app.
**Performance Goals**: Popover interactions must not fetch until Apply/remove/clear; value lists remain bounded; no unbounded DOM rendering for long lists.
**Constraints**: Preserve task-only visibility, avoid raw Temporal query authoring, keep direct browser calls to MoonMind APIs only, keep page size/pagination outside filters, and preserve `MM-588` traceability.
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
