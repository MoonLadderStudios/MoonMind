# Tasks: Mission Control Accessibility, Performance, and Fallback Posture

**Input**: Design documents from `specs/223-accessibility-performance-fallbacks/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `contracts/accessibility-fallbacks.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-429 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: Original Jira reference and preset brief are preserved in `spec.md`. This task list maps all `FR-*`, acceptance scenarios, edge cases, `SC-*`, and `DESIGN-REQ-*` evidence to concrete test, implementation, and verification work.

**Test Commands**:

- Unit tests: `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/lib/liquidGL/useLiquidGL.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx`
- Final verification: `moonspec-verify` (`/speckit.verify` user-facing equivalent)

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing frontend test and CSS inspection infrastructure is ready for MM-429.

- [X] T001 Confirm active feature artifacts exist for MM-429 in `specs/223-accessibility-performance-fallbacks/spec.md`, `plan.md`, `research.md`, `contracts/accessibility-fallbacks.md`, and `quickstart.md`
- [X] T002 Confirm `frontend/src/entrypoints/mission-control.test.tsx` can read `frontend/src/styles/mission-control.css` through the existing PostCSS helper before adding MM-429 tests
- [X] T003 [P] Confirm `frontend/src/lib/liquidGL/useLiquidGL.test.tsx` remains available for liquidGL fallback hook verification

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the traceability and test harness boundaries that block story work.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T004 Map MM-429 requirement IDs and source IDs into a local checklist in `specs/223-accessibility-performance-fallbacks/tasks.md` before editing tests
- [X] T005 Identify representative Mission Control selectors for contrast, focus, reduced motion, backdrop-filter fallback, liquidGL fallback, and dense-region premium-effect limits in `frontend/src/styles/mission-control.css`
- [X] T006 Confirm no backend, Temporal, Jira, or task submission payload files are required for this story by checking `specs/223-accessibility-performance-fallbacks/plan.md`

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Accessible Performance Fallbacks

**Summary**: As a Mission Control operator, I want readable contrast, visible keyboard focus, reduced-motion paths, and advanced-effect fallbacks so the interface remains usable across browsers, devices, motion preferences, and power conditions.

**Independent Test**: Render representative Mission Control routes with normal settings, reduced-motion preferences, disabled/unavailable backdrop filtering, and disabled/unavailable liquidGL enhancement. The story passes when controls remain keyboard-operable, readable, and visually coherent in every mode while existing task workflows keep working.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-015, DESIGN-REQ-022, DESIGN-REQ-023

**Test Plan**:

- Unit: CSS contract and liquidGL hook tests for contrast-bearing tokens, focus-visible coverage, reduced-motion suppression, backdrop-filter fallback, liquidGL fallback, and premium-effect containment.
- Integration: rendered route tests for task list, create page, and task detail/evidence behavior to prove existing workflows still pass after CSS changes.

### Unit Tests (write first)

> NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason when they expose a gap, then implement only enough code to make them pass.

- [X] T007 Add failing MM-429 contrast contract test for labels, table text, placeholders, chips, buttons, focus states, and glass-over-gradient surfaces covering FR-001, SC-001, DESIGN-REQ-015 in `frontend/src/entrypoints/mission-control.test.tsx`
- [X] T008 Add failing MM-429 focus-visible coverage test for representative interactive surfaces covering FR-002, SC-002, DESIGN-REQ-022 in `frontend/src/entrypoints/mission-control.test.tsx`
- [X] T009 Add failing MM-429 reduced-motion test for routine controls and running/live pulse effects covering FR-003, FR-008, SC-003, DESIGN-REQ-006 in `frontend/src/entrypoints/mission-control.test.tsx`
- [X] T010 Add failing MM-429 backdrop-filter fallback test for glass controls, liquid hero surfaces, and floating bars covering FR-004, SC-004, DESIGN-REQ-003, DESIGN-REQ-022 in `frontend/src/entrypoints/mission-control.test.tsx`
- [X] T011 Add failing MM-429 liquidGL fallback and CSS shell test covering FR-005, SC-004, DESIGN-REQ-003, DESIGN-REQ-022 in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T012 Add failing MM-429 premium-effect containment test for dense reading, table, form, evidence, log, and editing regions covering FR-006, FR-007, SC-005, DESIGN-REQ-023 in `frontend/src/entrypoints/mission-control.test.tsx`
- [X] T013 Add or update liquidGL unavailable/error-path hook verification covering FR-005 and SC-004 in `frontend/src/lib/liquidGL/useLiquidGL.test.tsx`
- [X] T014 Run `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/liquidGL/useLiquidGL.test.tsx` and capture the expected red-first failures for T007-T013

### Integration Tests (write first)

- [X] T015 Add or confirm task-list regression expectations for readable table/chip/focus behavior covering FR-001, FR-002, FR-009 in `frontend/src/entrypoints/tasks-list.test.tsx`
- [X] T016 Add or confirm create-page regression expectations for CSS-complete liquidGL fallback controls covering FR-005, FR-009 in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T017 Add or confirm task-detail/evidence regression expectations for matte dense evidence/log regions covering FR-007, FR-009 in `frontend/src/entrypoints/task-detail.test.tsx`
- [X] T018 Run `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx` and capture the expected red-first failures or existing-pass verification for T015-T017

### Implementation

- [X] T019 Conditionally update contrast token or selector rules for FR-001 in `frontend/src/styles/mission-control.css` only if T007 exposes a gap
- [X] T020 Conditionally update focus-visible selector coverage for FR-002 in `frontend/src/styles/mission-control.css` only if T008 exposes a gap
- [X] T021 Update reduced-motion CSS for running/live pulse and nonessential motion effects covering FR-003, FR-008, DESIGN-REQ-006 in `frontend/src/styles/mission-control.css`
- [X] T022 Conditionally update `@supports not ((backdrop-filter...))` fallback coverage for FR-004 in `frontend/src/styles/mission-control.css` only if T010 exposes a gap
- [X] T023 Conditionally update liquidGL fallback shell styling for FR-005 in `frontend/src/styles/mission-control.css` only if T011 exposes a gap
- [X] T024 Conditionally update `frontend/src/lib/liquidGL/useLiquidGL.ts` only if T013 exposes unsafe unavailable/error behavior for FR-005
- [X] T025 Conditionally remove or relocate heavy premium effects from dense selectors for FR-006 and FR-007 in `frontend/src/styles/mission-control.css` only if T012 or T017 exposes a gap
- [X] T026 Run `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/liquidGL/useLiquidGL.test.tsx` and fix MM-429 unit/CSS failures
- [X] T027 Run `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx` and fix task workflow regressions
- [X] T028 Mark completed implementation tasks in `specs/223-accessibility-performance-fallbacks/tasks.md` after tests pass

**Checkpoint**: The story is fully functional, covered by unit and integration-style UI tests, and testable independently.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without changing its core scope.

- [X] T029 [P] Review MM-429 traceability in `specs/223-accessibility-performance-fallbacks/spec.md`, `plan.md`, `tasks.md`, and `contracts/accessibility-fallbacks.md`
- [X] T030 Run quickstart validation commands from `specs/223-accessibility-performance-fallbacks/quickstart.md`
- [X] T031 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/liquidGL/useLiquidGL.test.tsx frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx` for final focused unit evidence
- [X] T032 Run `moonspec-verify` final verification and write the result to `specs/223-accessibility-performance-fallbacks/verification.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS story work
- **Story (Phase 3)**: Depends on Foundational phase completion
- **Polish (Phase 4)**: Depends on story tests and implementation passing

### Within The Story

- Unit and integration tests must be authored before implementation tasks.
- Red-first or verification-first runs must complete before conditional fallback implementation.
- `mission-control.css` changes are centralized, so CSS implementation tasks should be sequenced rather than parallelized.
- Route regression tests run after CSS changes.
- Final `moonspec-verify` work runs only after tests pass and tasks are marked complete.

### Parallel Opportunities

- T003 can run in parallel with T002.
- T007, T008, T009, T010, and T012 touch the same test file and should be edited in one ordered batch, not parallel.
- T011 and T013 touch separate test files and can be authored in parallel with the `mission-control.test.tsx` batch.
- T015, T016, and T017 touch separate route test files and can be checked in parallel after the unit contract tests are drafted.
- T029 can run in parallel with final validation command preparation.

## Parallel Example: Story Phase

```bash
# Separate files, safe to author together after Phase 2:
Task: "Add MM-429 CSS contract tests in frontend/src/entrypoints/mission-control.test.tsx"
Task: "Add MM-429 liquidGL hook test in frontend/src/lib/liquidGL/useLiquidGL.test.tsx"
Task: "Confirm create-page fallback shell test in frontend/src/entrypoints/task-create.test.tsx"
```

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 to lock the MM-429 traceability inventory.
2. Add focused MM-429 CSS/unit tests first.
3. Add or confirm route-level regression tests for task list, create page, and task detail/evidence.
4. Run the focused tests and record red-first failures or existing-pass verification.
5. Implement only the CSS/hook changes needed by failing MM-429 tests.
6. Run targeted unit and integration-style UI tests.
7. Mark tasks complete and run final `moonspec-verify`.

## Notes

- This task list covers one story only: MM-429 accessibility, performance, and fallback posture.
- Backend, Temporal, Jira, and task submission payload changes are out of scope unless a regression test exposes a direct break.
- `implemented_unverified` rows require verification tests first and conditional implementation only if verification fails.
- Preserve MM-429 and all source design IDs in final evidence.
