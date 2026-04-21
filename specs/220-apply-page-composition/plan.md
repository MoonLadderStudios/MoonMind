# Implementation Plan: Mission Control Page-Specific Task Workflow Composition

**Branch**: `run-jira-orchestrate-for-mm-428-apply-page-composition` | **Date**: 2026-04-21 | **Spec**: `specs/220-apply-page-composition/spec.md`
**Input**: Single-story runtime spec from trusted MM-428 Jira preset brief.

## Summary

Implement and verify route-specific task workflow page composition for MM-428. Prior MM-426/MM-427 work already provides strong task-list and create-page foundations, but MM-428 requires one cross-route story that proves `/tasks/list`, `/tasks/new`, and task detail/evidence-heavy pages all follow section 11 of `docs/UI/MissionControlDesignSystem.md`. Planned work is test-first: extend focused Vitest coverage for create/detail composition, add explicit detail/evidence section classes and data attributes where current markup is underspecified, then run targeted UI tests and final verification.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/tasks-list.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx` | preserve current task-list control deck | targeted UI regression |
| FR-002 | implemented_verified | `frontend/src/entrypoints/tasks-list.tsx`, `frontend/src/styles/mission-control.css`, `frontend/src/entrypoints/tasks-list.test.tsx` | preserve active chips/sticky table/pagination | targeted UI regression |
| FR-003 | implemented_verified | `frontend/src/entrypoints/task-create.tsx` uses `queue-steps-section` and step sections | new focused create-page composition tests passed | unit + integration-style UI |
| FR-004 | implemented_verified | `queue-floating-bar queue-floating-bar--liquid-glass` and existing create-page tests | preserve one floating launch rail | targeted UI regression |
| FR-005 | implemented_verified | `queue-submit-primary` and global textarea CSS exist | new focused create-page CTA and textarea tests passed | unit + integration-style UI |
| FR-006 | implemented_verified | `frontend/src/entrypoints/task-detail.tsx` has summary/fact/step/log/artifact sections but lacks clear composition markers | detail/evidence composition markers and tests implemented | unit + integration-style UI |
| FR-007 | implemented_verified | live logs and artifacts use table/viewer shells; dense sections are not explicitly marked matte/evidence | matte evidence-region CSS and tests implemented | unit + integration-style UI |
| FR-008 | implemented_verified | responsive CSS exists for task list/create; detail dense regions need explicit wrapping classes | wrapping/readability CSS and tests implemented | unit + integration-style UI |
| FR-009 | implemented_verified | existing task-list/create/detail behavior tests exist | run targeted regression tests after markup changes | targeted UI regression |
| FR-010 | implemented_verified | no single MM-428 cross-route coverage yet | focused route-composition tests passed | unit + integration-style UI |
| FR-011 | implemented_verified | `spec.md` preserves MM-428 brief and source IDs | preserve through tasks and verification | final verify |
| DESIGN-REQ-014 | implemented_verified | MM-426 task-list work | preserve | targeted UI regression |
| DESIGN-REQ-017 | implemented_verified | create floating rail exists; detail evidence surfaces need stronger no-competing-glass posture | detail CSS/classes | unit + integration-style UI |
| DESIGN-REQ-019 | implemented_verified | MM-426 task-list data slab work | preserve | targeted UI regression |
| DESIGN-REQ-020 | implemented_verified | create page has step section and floating bar | add explicit MM-428 tests | unit + integration-style UI |
| DESIGN-REQ-021 | implemented_verified | detail page sections exist but are not explicitly composition-safe | add section markers/classes and tests | unit + integration-style UI |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected for this story.  
**Primary Dependencies**: React, TanStack Query, existing Mission Control stylesheet, Vitest, Testing Library.  
**Storage**: No new persistent storage.  
**Unit Testing**: Vitest via `npm run ui:test -- <paths>` or direct local Vitest binary.  
**Integration Testing**: Rendered React entrypoint tests act as integration-style coverage for UI behavior; no compose-backed integration is required because backend contracts and persistence are unchanged.  
**Target Platform**: Browser Mission Control UI.  
**Project Type**: Frontend route composition and CSS.  
**Performance Goals**: No additional data fetches or heavy runtime effects; composition changes must not increase task workflow network calls.  
**Constraints**: Preserve existing task submission payloads, task detail actions, route navigation, Jira Orchestrate behavior, and Temporal contracts.  
**Scale/Scope**: One story spanning three task workflow page families: list, create, and detail/evidence.

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The story changes Mission Control presentation only and does not alter agent/provider orchestration.
- **II. One-Click Agent Deployment**: PASS. No deployment or prerequisite changes.
- **III. Avoid Vendor Lock-In**: PASS. No provider-specific behavior is introduced.
- **IV. Own Your Data**: PASS. Data remains in existing task APIs and artifacts.
- **V. Skills Are First-Class**: PASS. No skill runtime change.
- **Testing Discipline**: PASS. The plan requires unit and integration-style UI tests before implementation.

## Project Structure

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
frontend/src/entrypoints/task-detail.tsx
frontend/src/entrypoints/task-detail.test.tsx
frontend/src/entrypoints/tasks-list.tsx
frontend/src/entrypoints/tasks-list.test.tsx
frontend/src/styles/mission-control.css
specs/220-apply-page-composition/
```

## Complexity Tracking

No constitution violations or extra architectural complexity are required.
