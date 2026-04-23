# Tasks: Shared Executing Shimmer for Status Pills

**Input**: Design documents from `specs/244-shimmer-sweep-status-pill/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/status-pill-shimmer.md`, `quickstart.md`

## Validation Commands

- Unit tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts`
- Integration tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`
- Full unit suite: `./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Source Traceability Summary

- MM-488: Attach executing shimmer as a shared status-pill modifier.
- FR-001 through FR-009: shared executing shimmer, selector contract, executing-only isolation, content/layout preservation, reduced-motion behavior, calm active tone, reuse across surfaces, and traceability.
- SCN-001 through SCN-005: shared executing treatment, preferred/fallback selector paths, non-executing guardrails, text/layout stability, and reduced-motion replacement.
- SC-001 through SC-006: cross-surface verification, selector-path verification, non-executing exclusion, no layout/live-update regressions, reduced-motion fallback, and MM-488 traceability.
- DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-011, DESIGN-REQ-013, DESIGN-REQ-016: calm active intent, limited scope, selector contract, additive host behavior, shared modifier shape, reduced-motion fallback, and executing-only state matrix.
- Requirement status summary from `plan.md`: 11 `partial`, 11 `missing`, 5 `implemented_unverified`, 0 `implemented_verified`.

## Phase 1: Setup

- [ ] T001 Verify MM-488 planning inputs and target frontend files in `specs/244-shimmer-sweep-status-pill/spec.md`, `specs/244-shimmer-sweep-status-pill/plan.md`, `frontend/src/styles/mission-control.css`, `frontend/src/utils/executionStatusPillClasses.ts`, `frontend/src/entrypoints/tasks-list.tsx`, and `frontend/src/entrypoints/task-detail.tsx`.
- [ ] T002 Create the focused unit-test file scaffold for MM-488 in `frontend/src/utils/executionStatusPillClasses.test.ts` so helper-level selector and traceability assertions have an isolated home.

## Phase 2: Foundational

- [ ] T003 Confirm no new package, service, database, or compose-backed integration dependency is needed for MM-488 in `specs/244-shimmer-sweep-status-pill/plan.md` and `specs/244-shimmer-sweep-status-pill/research.md`.
- [ ] T004 Confirm the shared status-pill contract stays additive and page-neutral using `specs/244-shimmer-sweep-status-pill/contracts/status-pill-shimmer.md` and `specs/244-shimmer-sweep-status-pill/data-model.md` before story implementation begins.

## Phase 3: Story - Shared Executing Shimmer Modifier

**Summary**: As a Mission Control user, I want executing status pills to share one shimmer treatment so active workflow progress reads consistently anywhere that status pills appear.

**Independent Test**: Put task list and task detail status pills into executing, non-executing, and reduced-motion conditions, then verify the executing state alone receives the shared shimmer modifier, reduced motion receives a non-animated active treatment, and text, icon, layout, polling, and live-update behavior remain unchanged while MM-488 traceability is preserved.

**Traceability IDs**: FR-001 through FR-009; SCN-001 through SCN-005; SC-001 through SC-006; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-011, DESIGN-REQ-013, DESIGN-REQ-016.

### Unit Test Plan

- Helper tests verify executing-only selector metadata, preferred and fallback hook support, non-executing exclusion, and MM-488 traceability.
- CSS contract tests verify shared shimmer selectors, calm active token usage, reduced-motion fallback, bounded additive styling, and non-goal guardrails.

### Integration Test Plan

- Task list entrypoint tests verify table and card executing pills opt into the shared modifier while non-executing pills stay unchanged.
- Task detail entrypoint tests verify execution status pills opt into the same modifier and preserve visible text/layout behavior under executing and reduced-motion conditions.

### Tests First

- [ ] T005 [P] Add failing helper-level unit tests for executing-only selector metadata, preferred/fallback hook support, and MM-488 traceability in `frontend/src/utils/executionStatusPillClasses.test.ts` covering FR-002, FR-003, FR-009, SCN-002, SCN-003, SC-002, SC-003, SC-006, DESIGN-REQ-003, DESIGN-REQ-016.
- [ ] T006 [P] Add failing shared CSS contract tests for shimmer selectors, calm active token usage, additive bounded styling, and reduced-motion fallback in `frontend/src/entrypoints/mission-control.test.tsx` covering FR-001, FR-005, FR-006, FR-007, SC-001, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-004, DESIGN-REQ-011, DESIGN-REQ-013.
- [ ] T007 [P] Add failing task-list integration tests for executing table/card pills, non-executing exclusion, and selector-path reuse in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-001, FR-002, FR-003, FR-008, SCN-001, SCN-002, SCN-003, SC-001, SC-002, SC-003, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-016.
- [ ] T008 [P] Add failing task-detail integration tests for executing detail pills, text/layout stability, and reduced-motion active treatment in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-004, FR-005, FR-006, FR-008, SCN-004, SCN-005, SC-004, SC-005, DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-013.
- [ ] T009 Run the focused unit and integration Vitest commands from `specs/244-shimmer-sweep-status-pill/quickstart.md` to confirm T005-T008 fail for the expected missing MM-488 behavior before production changes.

### Conditional Fallback For Implemented-Unverified Rows

- [ ] T010 If T007-T009 expose regressions in visible text, icon choice, layout footprint, polling, or live-update behavior, update `frontend/src/entrypoints/tasks-list.tsx`, `frontend/src/entrypoints/task-detail.tsx`, and `frontend/src/styles/mission-control.css` to preserve FR-004, FR-005, SCN-004, SC-004, and DESIGN-REQ-004 while keeping the shimmer additive.

### Implementation

- [ ] T011 Implement executing-selector metadata and MM-488 traceability exports in `frontend/src/utils/executionStatusPillClasses.ts` for FR-002, FR-003, FR-009, SC-002, SC-003, SC-006, DESIGN-REQ-003, DESIGN-REQ-016.
- [ ] T012 Implement the shared shimmer modifier, reduced-motion fallback, calm active token binding, and executing-only CSS guardrails in `frontend/src/styles/mission-control.css` for FR-001, FR-003, FR-005, FR-006, FR-007, SC-001, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-004, DESIGN-REQ-011, DESIGN-REQ-013, DESIGN-REQ-016.
- [ ] T013 [P] Implement task-list executing-pill opt-in markup for preferred/fallback selector support in `frontend/src/entrypoints/tasks-list.tsx` covering FR-001, FR-002, FR-003, FR-008, SCN-001, SCN-002, SCN-003, SC-001, SC-002, SC-003, DESIGN-REQ-002, DESIGN-REQ-003.
- [ ] T014 [P] Implement task-detail executing-pill opt-in markup for preferred/fallback selector support in `frontend/src/entrypoints/task-detail.tsx` covering FR-001, FR-002, FR-004, FR-008, SCN-001, SCN-004, SCN-005, SC-001, SC-004, SC-005, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004.

### Story Validation

- [ ] T015 Run the focused unit validation command from `specs/244-shimmer-sweep-status-pill/quickstart.md` and verify the helper/CSS tests for MM-488 pass.
- [ ] T016 Run the focused integration validation command from `specs/244-shimmer-sweep-status-pill/quickstart.md` and verify task list and task detail surfaces pass MM-488 executing/non-executing/reduced-motion checks.
- [ ] T017 Update MM-488 requirement-status evidence in `specs/244-shimmer-sweep-status-pill/plan.md` after T015-T016 pass so `partial`, `missing`, and `implemented_unverified` rows reflect the final evidence.

## Final Phase: Polish and Verification

- [ ] T018 Expand or tighten edge-case assertions for rapid state flips, executing-only isolation, and future-state non-goals in `frontend/src/entrypoints/mission-control.test.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx`, and `frontend/src/entrypoints/task-detail.test.tsx` as needed for FR-003, SCN-003, and DESIGN-REQ-016.
- [ ] T019 Run the quickstart validation steps from `specs/244-shimmer-sweep-status-pill/quickstart.md`.
- [ ] T020 Run `./tools/test_unit.sh` for final unit-test verification.
- [ ] T021 Run `/moonspec-verify` by creating `specs/244-shimmer-sweep-status-pill/verification.md` with MM-488 traceability, DESIGN-REQ coverage, test evidence, and final verdict.

## Dependencies and Execution Order

1. T001-T004 establish inputs, scope boundaries, and the no-new-dependencies baseline.
2. T005-T009 write tests first and confirm they fail before implementation.
3. T010 is conditional and only executes if the verification-first tasks expose regressions in the `implemented_unverified` rows.
4. T011-T014 implement helper, shared CSS, and surface wiring after red-first confirmation.
5. T015-T017 validate focused MM-488 behavior and update evidence.
6. T018-T021 complete polish, quickstart validation, full unit verification, and final `/moonspec-verify` work.

## Parallel Examples

- T005 and T006 can run in parallel because they touch `frontend/src/utils/executionStatusPillClasses.test.ts` and `frontend/src/entrypoints/mission-control.test.tsx` respectively.
- T007 and T008 can run in parallel because they touch different entrypoint test files.
- T013 and T014 can run in parallel after T011-T012 because they modify different entrypoint files.

## Implementation Strategy

Start from the shared status-pill helper and Mission Control stylesheet already in the repo. Write helper, CSS contract, and entrypoint render tests first, confirm they fail, then implement the smallest additive changes needed to expose the executing shimmer selector contract across list and detail surfaces. Keep the shimmer executing-only, preserve the existing visible pill content and layout, use reduced-motion CSS fallback instead of runtime state plumbing, and preserve MM-488 traceability through final verification.
