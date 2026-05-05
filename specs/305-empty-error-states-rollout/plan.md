# Implementation Plan: Empty/Error States and Regression Coverage for Final Rollout

**Branch**: `305-empty-error-states-rollout` | **Date**: 2026-05-05 | **Spec**: [spec.md](spec.md)
**Input**: Single-story runtime spec generated from the trusted Jira preset brief for `MM-592`.

## Summary

Complete the final Tasks List column-filter rollout story for `MM-592` by verifying recoverable loading, API error, empty first-page, empty later-page, facet failure, invalid-filter, old-control removal, and non-goal safety behavior. Current repo evidence now shows the story is implemented and verified in `frontend/src/entrypoints/tasks-list.tsx` and `frontend/src/entrypoints/tasks-list.test.tsx`: final regression coverage exists for loading/API-error/empty-first-page recovery, and structured API error detail rendering is implemented. The plan remains frontend-focused, with no new persistence and no canonical docs changes.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `tasks-list.tsx` renders `Loading tasks...`; `tasks-list.test.tsx` covers the pending request state | complete | focused UI passed |
| FR-002 | implemented_verified | `tasks-list.tsx` parses sanitized structured API error detail; `tasks-list.test.tsx` verifies `detail.message` display | complete | focused UI passed |
| FR-003 | implemented_verified | `tasks-list.tsx` renders empty first-page text; `tasks-list.test.tsx` covers active-filter empty state | complete | focused UI passed |
| FR-004 | implemented_verified | `tasks-list.test.tsx` verifies enabled Clear filters on empty first page with active filters | complete | focused UI passed |
| FR-005 | implemented_verified | `tasks-list.test.tsx` covers previous-page enabled on empty later pages | preserve behavior | final UI validation |
| FR-006 | implemented_verified | pagination tests cover next token and previous cursor stack behavior | preserve behavior | final UI validation |
| FR-007 | implemented_verified | `tasks-list.test.tsx` covers facet failure fallback notice and table usability | preserve behavior | final UI validation |
| FR-008 | implemented_verified | `tasks-list.test.tsx` covers contradictory canonical filter validation and Clear filters recovery | preserve behavior | final UI validation |
| FR-009 | implemented_verified | structured API error detail is surfaced and active filter controls remain available | complete | focused UI passed |
| FR-010 | implemented_verified | tests assert old control form and workflow-kind controls are absent | preserve behavior | final UI validation |
| FR-011 | implemented_verified | task-scope and no workflow-kind browsing tests cover non-goal safety | preserve behavior | final UI validation |
| FR-012 | implemented_verified | final rollout tests now cover loading, API error, empty first page, empty later page, facet fallback, invalid filters, old controls, and non-goals | complete | focused UI + full unit passed |
| FR-013 | implemented_verified | `spec.md`, `plan.md`, `tasks.md`, and `verification.md` preserve MM-592 | complete | artifact review passed |
| SC-001 | implemented_verified | pending request test asserts `Loading tasks...` | complete | focused UI passed |
| SC-002 | implemented_verified | structured error test asserts API detail message | complete | focused UI passed |
| SC-003 | implemented_verified | active-filter empty first page test asserts text and enabled Clear filters | complete | focused UI passed |
| SC-004 | implemented_verified | existing UI test covers empty later-page previous button | preserve behavior | final UI validation |
| SC-005 | implemented_verified | existing UI test covers facet fallback notice | preserve behavior | final UI validation |
| SC-006 | implemented_verified | local contradictory validation and structured API detail are covered | complete | focused UI passed |
| SC-007 | implemented_verified | old-control absence and workflow-kind safety tests exist | preserve behavior | final UI validation |
| SC-008 | implemented_verified | MM-592 and source design IDs are preserved across feature artifacts | complete | traceability check passed |
| DESIGN-REQ-006 | implemented_verified | loading, error, empty first-page, empty later-page, pagination, and page-size behavior are covered | complete | focused UI passed |
| DESIGN-REQ-024 | implemented_verified | empty recovery, facet fallback, local validation, and API validation detail are covered | complete | focused UI passed |
| DESIGN-REQ-026 | implemented_verified | final rollout regression set is present and passing | complete | focused UI + full unit passed |
| DESIGN-REQ-027 | implemented_verified | normal page exposes no raw Temporal query, system browsing, saved views, pivot/spreadsheet controls, or pagination replacement | preserve non-goals | final UI validation |
| DESIGN-REQ-028 | implemented_verified | old controls remain removed after parity tests; MM-592 feature-local artifacts and verification are complete | complete | artifact review passed |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected for production changes.  
**Primary Dependencies**: React, TanStack Query, Zod, Vitest, Testing Library, existing Mission Control stylesheet.  
**Storage**: No new persistent storage; state remains URL/query state and component state only.  
**Unit Testing**: Focused UI validation with `node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`; final runner `./tools/test_unit.sh` when feasible.  
**Integration Testing**: UI integration-style component tests in the Tasks List entrypoint test file; no compose-backed integration is required for this frontend-only story.  
**Target Platform**: Mission Control browser UI served by FastAPI.  
**Project Type**: Web frontend inside the MoonMind monorepo.  
**Performance Goals**: Error parsing must not add extra network calls; empty/error states must not remount the table in a way that loses filter recovery controls.  
**Constraints**: Keep `/tasks/list` task-scoped, preserve old-control removal, avoid raw Temporal query authoring, keep browser calls to MoonMind APIs only, and preserve `MM-592` traceability.  
**Scale/Scope**: One Tasks List React entrypoint and focused UI tests.

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. This story changes Mission Control UI behavior only.
- II. One-Click Agent Deployment: PASS. No deployment or external prerequisite changes.
- III. Avoid Vendor Lock-In: PASS. No provider-specific behavior.
- IV. Own Your Data: PASS. Browser calls remain MoonMind API calls only.
- V. Skills Are First-Class and Easy to Add: PASS. No skill runtime changes.
- VI. Replaceable Scaffolding: PASS. Behavior is pinned by focused regression tests.
- VII. Runtime Configurability: PASS. Existing poll/list config remains respected.
- VIII. Modular and Extensible Architecture: PASS. Changes stay inside the existing Tasks List UI module.
- IX. Resilient by Default: PASS. Recoverable errors and empty states improve operator recovery.
- X. Facilitate Continuous Improvement: PASS. Verification artifacts record evidence and blockers.
- XI. Spec-Driven Development: PASS. This plan follows `spec.md` and preserves the canonical `MM-592` input.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. Canonical docs are read as source requirements and not rewritten.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases or internal contract shims are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/305-empty-error-states-rollout/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── tasks-list-empty-error-rollout.md
├── checklists/
│   └── requirements.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/tasks-list.tsx
frontend/src/entrypoints/tasks-list.test.tsx
frontend/src/styles/mission-control.css
```

**Structure Decision**: Use the existing Tasks List entrypoint and test file. No backend, database, generated dist asset, or canonical documentation changes are required.

## Complexity Tracking

No constitution violations or added architectural complexity.
