# Tasks: Executing Text Brightening Sweep

**Input**: Design documents from `specs/259-executing-text-brightening/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/execution-status-pill.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are required. Red-first tasks author CSS contract and task-list render tests before component and stylesheet implementation.

## Validation Commands

- Unit tests: `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx`
- Managed-path unit equivalent: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx`
- Managed-path integration equivalent: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`
- Typecheck: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- Lint: `./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src`
- Full frontend suite: `./node_modules/.bin/vitest run --config frontend/vite.config.ts`
- Full unit suite: `./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Source Traceability Summary

- Single story: `Executing Letter Brightening` from `spec.md`.
- FR-001 through FR-011: all `implemented_verified` in `plan.md`; preserved through validation and final verification tasks.
- DESIGN-REQ-001 through DESIGN-REQ-008: all covered by `research.md`, `contracts/execution-status-pill.md`, implementation tasks, and final verification.
- SCN-001 through SCN-005: task-list table/card render behavior, non-executing isolation, accessibility, shared timing, and reduced-motion behavior.
- SC-001 through SC-005: focused UI tests, full frontend suite, typecheck/lint, and `verification.md`.

## Phase 1: Setup

- [X] T001 Verify the active one-story spec and planning artifact set exists under `specs/259-executing-text-brightening/` for FR-001 through FR-011 and DESIGN-REQ-001 through DESIGN-REQ-008.
- [X] T002 Identify the implementation surfaces in `frontend/src/components/ExecutionStatusPill.tsx`, `frontend/src/entrypoints/tasks-list.tsx`, `frontend/src/styles/mission-control.css`, `frontend/src/entrypoints/tasks-list.test.tsx`, and `frontend/src/entrypoints/mission-control.test.tsx`.
- [X] T003 Confirm frontend validation commands and managed-path local binary equivalents in `specs/259-executing-text-brightening/quickstart.md`.

## Phase 2: Foundational

- [X] T004 Confirm no new persistent storage, API contract, service fixture, Docker dependency, or backend foundation is required for this frontend-only story in `specs/259-executing-text-brightening/plan.md`.
- [X] T005 Confirm the existing `executionStatusPillProps()` helper remains the single status metadata boundary for DESIGN-REQ-007 before story implementation begins.

## Phase 3: Story - Executing Letter Brightening

**Summary**: As a Mission Control user, I want executing task-list status pills to brighten letters in sync with the existing shimmer sweep so active work feels visibly alive without extra polling or layout changes.

**Independent Test**: Render task-list rows in executing and non-executing states, then verify only executing pills use per-glyph visual spans with staggered CSS delays, preserve the shared status-pill metadata and accessible label, keep non-executing pills as plain text, and rely on the shared shimmer duration with reduced-motion suppression.

**Traceability IDs**: FR-001 through FR-011; DESIGN-REQ-001 through DESIGN-REQ-008; SCN-001 through SCN-005; SC-001 through SC-005.

### Unit Test Plan

- Mission Control CSS contract tests verify the physical sweep remains, glyph-wave CSS uses the shared duration, `mm-executing-letter-brighten` exists, and reduced-motion disables glyph animation, text shadow, and filter.

### Integration Test Plan

- Task-list render tests verify both table and card executing pills receive accessible glyph-wave markup with per-glyph delays while non-executing statuses remain plain and do not receive executing metadata.

### Unit Tests (Red First)

- [X] T006 [P] Add failing CSS contract assertions in `frontend/src/entrypoints/mission-control.test.tsx` for FR-001, FR-003, FR-006, FR-008, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-006.
- [X] T007 Run the unit test command for `frontend/src/entrypoints/mission-control.test.tsx` before production CSS changes and record the expected pre-implementation failure or local dependency blocker.

### Integration Tests (Red First)

- [X] T008 [P] Add failing task-list render assertions in `frontend/src/entrypoints/tasks-list.test.tsx` for FR-002, FR-004, FR-007, FR-009, FR-010, FR-011, DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-007, and DESIGN-REQ-008.
- [X] T009 Run the integration test command for `frontend/src/entrypoints/tasks-list.test.tsx` before production component changes and record the expected pre-implementation failure or local dependency blocker.

### Implementation

- [X] T010 Implement `frontend/src/components/ExecutionStatusPill.tsx` for FR-002, FR-004, FR-005, FR-006, FR-007, FR-009, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, and DESIGN-REQ-007.
- [X] T011 Replace task-list table and card status spans in `frontend/src/entrypoints/tasks-list.tsx` for FR-010 and DESIGN-REQ-008 while preserving `row.rawState || row.state || row.status`.
- [X] T012 Update `frontend/src/styles/mission-control.css` for FR-001, FR-002, FR-003, FR-006, FR-008, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-006.
- [X] T013 Update `docs/UI/EffectShimmerSweep.md` so canonical UI documentation describes the foreground glyph text-brightening layer for DESIGN-REQ-001 through DESIGN-REQ-006.

### Story Validation

- [X] T014 Run focused unit and integration validation through `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/mission-control.test.tsx` and record evidence in `specs/259-executing-text-brightening/verification.md`.
- [X] T015 Run `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` and `./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src` for SC-004 and record evidence in `specs/259-executing-text-brightening/verification.md`.
- [X] T016 Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts` for full frontend regression coverage and record evidence in `specs/259-executing-text-brightening/verification.md`.

## Final Phase: Polish And Verification

- [X] T017 Refresh `specs/259-executing-text-brightening/plan.md`, `specs/259-executing-text-brightening/research.md`, and `specs/259-executing-text-brightening/quickstart.md` after upstream planning drift so unit and integration strategies remain explicit.
- [X] T018 Refresh `specs/259-executing-text-brightening/tasks.md` after upstream artifact changes so the task list still covers exactly one story with red-first unit tests, integration tests, implementation tasks, story validation, and final `/moonspec-verify`.
- [X] T019 Run `/moonspec-verify` by maintaining `specs/259-executing-text-brightening/verification.md` with FR, SC, and DESIGN-REQ coverage plus test evidence after implementation and validation pass.

## Dependencies and Execution Order

1. T001-T005 establish the active one-story scope, frontend seams, validation commands, and status-helper boundary.
2. T006-T009 write red-first unit and integration tests before production implementation.
3. T010-T013 implement the component, task-list integration, CSS glyph layer, and canonical UI documentation.
4. T014-T016 validate the story through focused tests, static checks, and full frontend regression coverage.
5. T017-T019 refresh MoonSpec artifacts and complete final `/moonspec-verify` evidence.

## Parallel Examples

- T006 and T008 can run in parallel because they touch different test files.
- T010 and T012 can run in parallel after red-first confirmation because they touch different production files, but T011 depends on T010's exported component.

## Implementation Strategy

The story is already implemented and verified, so this refreshed task list preserves the completed TDD sequence rather than adding hidden scope. Future re-execution should start from T006/T008 for red-first coverage, proceed through the component/CSS/task-list implementation tasks, then finish with T014-T019 validation and `/moonspec-verify`.
