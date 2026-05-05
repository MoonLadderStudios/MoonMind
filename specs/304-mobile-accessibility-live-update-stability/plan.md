# Implementation Plan: Mobile, Accessibility, and Live-Update Stability

**Branch**: `304-mobile-accessibility-live-update-stability` | **Date**: 2026-05-05 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/304-mobile-accessibility-live-update-stability/spec.md`

## Summary

Complete the Tasks List runtime UI story for `MM-591` by closing the remaining gaps in mobile filter reachability, desktop filter keyboard/focus behavior, and live-update stability. Current repo evidence shows the implementation now adds ID and Title text filters to the shared column-filter model, exposes them in mobile controls, pauses list polling while a desktop filter editor is open, and manages focus for keyboard/dialog closure. Validation is focused in the existing Tasks List Vitest suite, with the full unit runner as final verification.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/tasks-list.tsx` defines `taskId` and `title` text filters; `tasks-list.test.tsx` asserts mobile ID and Title controls plus canonical query params | preserve behavior | final UI validation |
| FR-002 | implemented_verified | mobile filter test asserts task-scoped URL with ID/Title filters and no stale pagination cursor | preserve behavior | final UI validation |
| FR-003 | implemented_verified | separate sort and filter buttons plus existing tests in `tasks-list.test.tsx` | preserve behavior | final UI validation |
| FR-004 | implemented_verified | `tasks-list.tsx` tracks the originating filter button and pending focus field; `tasks-list.test.tsx` verifies focus moves into the Title filter dialog | preserve behavior | final UI validation |
| FR-005 | implemented_verified | existing staging tests cover cancel, Escape, outside click without requests | preserve behavior while changing close helper | final UI validation |
| FR-006 | implemented_verified | `tasks-list.tsx` applies staged non-textarea filter edits on Enter; `tasks-list.test.tsx` verifies Title text filter Enter apply updates the URL | preserve behavior | final UI validation |
| FR-007 | implemented_verified | `tasks-list.tsx` disables `refetchInterval` while `openFilter` is set, preserving staged editor state | preserve behavior | final UI validation / code inspection |
| FR-008 | implemented_verified | active chips, filter accessible names, `aria-sort`, and status pill labels are covered by existing tests | preserve behavior | final UI validation |
| FR-009 | implemented_verified | task scope normalization and absent workflow-kind controls are covered by existing tests | preserve behavior | final UI validation |
| FR-010 | implemented_verified | `spec.md`, `plan.md`, `tasks.md`, and `verification.md` preserve `MM-591` and the canonical Jira preset brief | preserve traceability | artifact review |
| SC-001 | implemented_verified | mobile controls test asserts ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, and Finished controls | preserve behavior | final UI validation |
| SC-002 | implemented_verified | mobile URL assertion includes task scope, ID/Title filters, status/runtime/repository filters, and omits stale cursor state | preserve behavior | final UI validation |
| SC-003 | implemented_verified | focused UI test verifies a desktop Title filter dialog receives focus on open | preserve behavior | final UI validation |
| SC-004 | implemented_verified | focused UI test verifies Enter applies staged Title text-filter changes | preserve behavior | final UI validation |
| SC-005 | implemented_verified | existing workflow-kind tests | preserve behavior | final UI validation |
| SC-006 | implemented_verified | artifact review confirms `MM-591`, canonical preset brief, and source design IDs are preserved | preserve traceability | artifact review |
| DESIGN-REQ-006 | implemented_verified | mobile card structure and details-action tests already exist | preserve behavior | final UI validation |
| DESIGN-REQ-021 | implemented_verified | staged filters exist and polling is paused while a filter editor is open | preserve behavior | final UI validation / code inspection |
| DESIGN-REQ-022 | implemented_verified | sort/filter ARIA, Escape/cancel staging, focus-in, focus-return helper, Enter apply, and non-color active indicators are present | preserve behavior | final UI validation |
| DESIGN-REQ-023 | implemented_verified | mobile filters expose status, runtime, skill, repository, title, ID, and date filtering through task-scoped semantics | preserve behavior | final UI validation |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not part of this story  
**Primary Dependencies**: React, TanStack Query, Zod, Vitest, Testing Library, existing Mission Control CSS  
**Storage**: N/A; filter state is URL/query state and component state only  
**Unit Testing**: Vitest via `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx`; final repo runner `./tools/test_unit.sh`  
**Integration Testing**: UI integration-style component tests in `frontend/src/entrypoints/tasks-list.test.tsx`; no compose-backed integration needed for this frontend-only story  
**Target Platform**: Mission Control browser UI served by FastAPI  
**Project Type**: Web frontend inside the MoonMind monorepo  
**Performance Goals**: Preserve bounded polling and avoid refetching list data while an editor is open  
**Constraints**: Ordinary `/tasks/list` must remain task-scoped and must not expose system workflows; mobile filters must not depend on desktop table headers  
**Scale/Scope**: One Tasks List page entrypoint and its focused UI test file

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. This story changes Mission Control UI behavior only and does not replace agent orchestration.
- II. One-Click Agent Deployment: PASS. No deployment or external prerequisite changes.
- III. Avoid Vendor Lock-In: PASS. No provider-specific behavior.
- IV. Own Your Data: PASS. Browser calls remain MoonMind API only.
- V. Skills Are First-Class and Easy to Add: PASS. No skill runtime changes.
- VI. Replaceable Scaffolding: PASS. Behavior is covered by focused tests instead of brittle manual checks.
- VII. Runtime Configurability: PASS. Existing poll interval config remains respected.
- VIII. Modular and Extensible Architecture: PASS. Changes stay inside the existing Tasks List UI module.
- IX. Resilient by Default: PASS. Filter editing avoids live-update disruption.
- X. Facilitate Continuous Improvement: PASS. Verification artifacts record outcome and test evidence.
- XI. Spec-Driven Development: PASS. This spec, plan, tasks, and verification preserve the canonical `MM-591` input.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. Canonical docs are read as source requirements and not rewritten.
- XIII. Pre-Release Compatibility Policy: PASS. No internal compatibility aliases or deprecated paths are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/304-mobile-accessibility-live-update-stability/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── tasks-list-filter-behavior.md
├── checklists/
│   └── requirements.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/
└── src/
    ├── entrypoints/
    │   ├── tasks-list.tsx
    │   └── tasks-list.test.tsx
    └── styles/
        └── mission-control.css
```

**Structure Decision**: Use the existing Tasks List entrypoint and test file. No backend or persistent data model changes are required because this story uses existing execution-list query parameters and component state.

## Complexity Tracking

No constitution violations or added architectural complexity.
