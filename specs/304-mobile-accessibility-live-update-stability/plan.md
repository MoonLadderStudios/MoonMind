# Implementation Plan: Mobile, Accessibility, and Live-Update Stability

**Branch**: `304-mobile-accessibility-live-update-stability` | **Date**: 2026-05-05 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/304-mobile-accessibility-live-update-stability/spec.md`

## Summary

Complete the Tasks List runtime UI story for `MM-591` by closing the remaining gaps in mobile filter reachability, desktop filter keyboard/focus behavior, and live-update stability. Existing column-filter work already handles task-only visibility, legacy URL normalization, status/runtime/skill/repository/date filters, active chips, and desktop sort/filter separation. The implementation adds ID and Title text filters to the same column-filter model, exposes them in mobile controls, pauses list polling while a desktop filter editor is open, and returns focus after keyboard/dialog closure. Validation is focused in the existing Tasks List Vitest suite, with the full unit runner as final verification.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `frontend/src/entrypoints/tasks-list.tsx` had mobile status/runtime/skill/repository/date filters but no ID or Title filters | add ID and Title text filters to shared filter model and mobile controls | UI unit |
| FR-002 | implemented_unverified | existing `applyFilters()` resets cursor stack; mobile controls use it | extend mobile filter test to include ID/Title and task-scoped request URL | UI unit |
| FR-003 | implemented_verified | separate sort and filter buttons plus existing tests in `tasks-list.test.tsx` | preserve behavior | final UI validation |
| FR-004 | partial | dialogs supported Escape/cancel but did not manage focus return | add focus-in/focus-return behavior and keyboard apply coverage | UI unit |
| FR-005 | implemented_verified | existing staging tests cover cancel, Escape, outside click without requests | preserve behavior while changing close helper | final UI validation |
| FR-006 | missing | no Enter-to-apply coverage found | add Enter handler for non-textarea dialog targets | UI unit |
| FR-007 | partial | drafts are staged, but list polling continued while a popover was open | pause list refetch interval while a filter editor is open | UI unit / code inspection |
| FR-008 | implemented_verified | active chips, filter accessible names, `aria-sort`, and status pill labels are covered by existing tests | preserve behavior | final UI validation |
| FR-009 | implemented_verified | task scope normalization and absent workflow-kind controls are covered by existing tests | preserve behavior | final UI validation |
| FR-010 | missing | no `MM-591` feature artifacts existed | create MoonSpec artifacts preserving Jira brief | artifact review |
| SC-001 | partial | mobile controls test existed but missed ID/Title | extend mobile controls test | UI unit |
| SC-002 | implemented_unverified | URL assertion covered status/repo/runtime | extend assertion for ID/Title and cursor omission | UI unit |
| SC-003 | missing | no focus test found | add focus test | UI unit |
| SC-004 | implemented_unverified | status staging covered Apply button only | add Enter apply text-filter test | UI unit |
| SC-005 | implemented_verified | existing workflow-kind tests | preserve behavior | final UI validation |
| SC-006 | missing | no `MM-591` artifacts existed | preserve issue key in spec, plan, tasks, verification | artifact review |
| DESIGN-REQ-006 | implemented_verified | mobile card structure and details-action tests already exist | preserve behavior | final UI validation |
| DESIGN-REQ-021 | partial | staged filters existed, polling pause missing | pause polling while editor is open | UI unit / code inspection |
| DESIGN-REQ-022 | partial | sort/filter ARIA and Escape existed, focus/Enter gaps remained | add focus and Enter behavior | UI unit |
| DESIGN-REQ-023 | partial | mobile parity existed for most fields, missing ID/Title | add ID/Title mobile filters | UI unit |

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
