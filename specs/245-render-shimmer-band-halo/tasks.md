# Tasks: Themed Shimmer Band and Halo Layers

**Input**: Design documents from `specs/245-render-shimmer-band-halo/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/status-pill-shimmer-layers.md`, `quickstart.md`

## Validation Commands

- Unit tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts`
- Integration tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`
- Full unit suite: `./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Source Traceability Summary

- MM-489: Render the themed shimmer band and halo layers.
- FR-001 through FR-007: preserved base appearance, dual-layer shimmer, theme-token binding, text readability, bounded/non-interfering treatment, reusable effect tokens, and MM-489 traceability.
- SCN-001 through SCN-005: additive base preservation, bright-band/wider-halo semantics, theme coherence, interaction safety, and bounded in-pill rendering.
- SC-001 through SC-007: measurable proof for base visibility, dual-layer semantics, theme coherence, readability, bounded layout, token surface, and MM-489 traceability.
- DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-012, DESIGN-REQ-015: legibility-first design, layered visual model, theme binding, isolation rules, attachable host treatment, and reusable token surface.
- Requirement status summary from `plan.md`: 2 `missing`, 3 `partial`, 20 `implemented_unverified`, 0 `implemented_verified`.

## Phase 1: Setup

- [ ] T001 Verify MM-489 planning inputs and target frontend files in `specs/245-render-shimmer-band-halo/spec.md`, `specs/245-render-shimmer-band-halo/plan.md`, `specs/245-render-shimmer-band-halo/research.md`, `frontend/src/styles/mission-control.css`, `frontend/src/utils/executionStatusPillClasses.ts`, `frontend/src/entrypoints/tasks-list.tsx`, and `frontend/src/entrypoints/task-detail.tsx`.
- [ ] T002 Confirm the focused unit and integration test commands in `specs/245-render-shimmer-band-halo/quickstart.md` remain the active validation path for MM-489 and require no new package or service setup.

## Phase 2: Foundational

- [ ] T003 Reconcile the MM-489 layered shimmer contract and data model in `specs/245-render-shimmer-band-halo/contracts/status-pill-shimmer-layers.md` and `specs/245-render-shimmer-band-halo/data-model.md` with the current shared status-pill seams before story verification begins.
- [ ] T004 Confirm the story stays within the existing shared Mission Control pill surfaces and requires no new component, route, or infrastructure in `specs/245-render-shimmer-band-halo/plan.md` and `specs/245-render-shimmer-band-halo/research.md`.

## Phase 3: Story - Premium Executing Shimmer Layers

**Summary**: As a Mission Control user, I want the executing shimmer treatment to render a distinct bright band and trailing halo so active progress feels premium while status text remains readable.

**Independent Test**: Put an executing status pill into light theme and dark theme contexts, then verify the treatment keeps the base appearance visible, renders a bright sweep band with a wider dimmer halo, preserves text readability and interaction behavior, stays bounded to the pill, exposes reusable effect tokens or equivalent variables, and preserves MM-489 traceability.

**Traceability IDs**: FR-001 through FR-007; SCN-001 through SCN-005; SC-001 through SC-007; DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-012, DESIGN-REQ-015.

### Unit Test Plan

- CSS contract tests verify preserved base appearance, dual-layer band/halo semantics, theme-token binding, bounded additive styling, and reusable token/variable coverage.
- Helper tests verify the shared selector contract remains stable while preserving MM-489-specific traceability.

### Integration Test Plan

- Task list and task detail render tests verify the existing shared executing-pill surfaces keep visible text unchanged, remain bounded to the pill footprint, and continue to use the same selector path while proving MM-489-specific behavior.

### Tests First

- [ ] T005 [P] Add failing CSS contract tests for preserved base appearance, bright-band/wider-halo semantics, theme-token binding, bounded additive behavior, and reusable effect tokens in `frontend/src/entrypoints/mission-control.test.tsx` covering FR-001, FR-002, FR-003, FR-005, FR-006, SCN-001, SCN-002, SCN-003, SCN-005, SC-001, SC-002, SC-003, SC-005, SC-006, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-012, DESIGN-REQ-015.
- [ ] T006 [P] Add failing helper tests for MM-489 traceability alongside the existing executing shimmer selector contract in `frontend/src/utils/executionStatusPillClasses.test.ts` covering FR-007, SC-007, and MM-489 traceability preservation.
- [ ] T007 [P] Add failing task-list integration tests for visible text preservation, bounded executing-pill rendering, and non-executing guardrails in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-001, FR-004, FR-005, SCN-001, SCN-004, SCN-005, SC-001, SC-004, SC-005, DESIGN-REQ-009, DESIGN-REQ-012.
- [ ] T008 [P] Add failing task-detail integration tests for visible text preservation, bounded executing-pill rendering, and shared selector reuse in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-004, FR-005, SCN-004, SCN-005, SC-004, SC-005, DESIGN-REQ-009, DESIGN-REQ-012.
- [ ] T009 Run the focused unit and integration commands from `specs/245-render-shimmer-band-halo/quickstart.md` to confirm T005-T008 fail for the expected MM-489 gaps before implementation.

### Conditional Fallback For Implemented-Unverified Rows

- [ ] T010 If T005 or T009 shows the layered visual model is incomplete, update `frontend/src/styles/mission-control.css` to preserve the executing base appearance and explicitly express the bright-band/wider-halo behavior for FR-001, FR-002, FR-003, SCN-001, SCN-002, SCN-003, SC-001, SC-002, SC-003, DESIGN-REQ-005, DESIGN-REQ-006, and DESIGN-REQ-008.
- [ ] T011 If T007-T009 shows text, bounds, or interaction regressions, update `frontend/src/styles/mission-control.css`, `frontend/src/entrypoints/tasks-list.tsx`, and `frontend/src/entrypoints/task-detail.tsx` to preserve FR-004, FR-005, SCN-004, SCN-005, SC-004, SC-005, DESIGN-REQ-009, and DESIGN-REQ-012 while keeping the shimmer additive.

### Implementation

- [ ] T012 Add the missing MM-489 traceability surface in `frontend/src/utils/executionStatusPillClasses.ts` and `frontend/src/utils/executionStatusPillClasses.test.ts` for FR-007 and SC-007.
- [ ] T013 Add reusable effect tokens or equivalent variables for the MM-489 layered shimmer in `frontend/src/styles/mission-control.css` for FR-006, SC-006, and DESIGN-REQ-015.
- [ ] T014 Re-run the focused unit command from `specs/245-render-shimmer-band-halo/quickstart.md`, fix any failing MM-489 CSS/helper assertions in `frontend/src/styles/mission-control.css` and `frontend/src/utils/executionStatusPillClasses.ts`, and confirm the verification-first tasks now pass.
- [ ] T015 Re-run the focused integration command from `specs/245-render-shimmer-band-halo/quickstart.md`, fix any failing MM-489 render assertions in `frontend/src/entrypoints/tasks-list.tsx` and `frontend/src/entrypoints/task-detail.tsx`, and confirm the story passes end-to-end on the supported surfaces.

### Story Validation

- [ ] T016 Update the MM-489 requirement-status evidence in `specs/245-render-shimmer-band-halo/plan.md` after T014-T015 so `missing`, `partial`, and `implemented_unverified` rows reflect the final proof.
- [ ] T017 Verify the independent story criteria in `specs/245-render-shimmer-band-halo/quickstart.md` and record any remaining MM-489-specific gaps before polish work.

## Final Phase: Polish and Verification

- [ ] T018 Expand edge-case coverage for non-executing guardrails, token-surface completeness, and bounded layered treatment in `frontend/src/entrypoints/mission-control.test.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx`, and `frontend/src/entrypoints/task-detail.test.tsx` as needed for SCN-003, SCN-005, SC-005, SC-006, and DESIGN-REQ-015.
- [ ] T019 Run the quickstart validation steps from `specs/245-render-shimmer-band-halo/quickstart.md`.
- [ ] T020 Run `./tools/test_unit.sh` for final unit-test verification.
- [ ] T021 Run `/moonspec-verify` by creating `specs/245-render-shimmer-band-halo/verification.md` with MM-489 traceability, DESIGN-REQ coverage, test evidence, and final verdict.

## Dependencies and Execution Order

1. T001-T004 establish the story inputs, scope boundaries, and no-new-infrastructure baseline.
2. T005-T009 write verification tests first and confirm they fail before code changes.
3. T010-T011 are conditional fallback tasks for the implemented-unverified rows and execute only if the verification-first tasks expose real gaps.
4. T012-T013 complete the planned missing and partial implementation work for MM-489 traceability and reusable token coverage.
5. T014-T017 rerun focused validation, fix remaining regressions, and update planning evidence.
6. T018-T021 complete edge-case coverage, quickstart validation, final unit verification, and `/moonspec-verify` work.

## Parallel Examples

- T005 and T006 can run in parallel because they touch `frontend/src/entrypoints/mission-control.test.tsx` and `frontend/src/utils/executionStatusPillClasses.test.ts` respectively.
- T007 and T008 can run in parallel because they modify different integration test files.
- T012 and T013 can run in parallel after T009 because they modify different implementation files.

## Implementation Strategy

Start from the shared executing shimmer already present in the repo and treat MM-489 as a verification-first refinement story. Prove the layered band-and-halo behavior, bounded additive rendering, and theme-token semantics with focused tests before changing code; only if those tests fail should the shared CSS or render seams be refined. Complete the known missing work by adding MM-489 traceability and the remaining reusable token surface, then finish with quickstart validation, the full unit suite, and `/moonspec-verify`.
