# Tasks: Mission Control Page-Specific Task Workflow Composition

**Input**: Design artifacts from `specs/220-apply-page-composition/`  
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `contracts/page-composition.md`, `quickstart.md`  
**Story**: Exactly one story, "Page-Specific Task Workflow Composition"  
**Independent Test**: Render task list, create page, and task detail/evidence pages with representative data and verify each route exposes the documented composition structure while existing behavior remains unchanged.  
**Source Traceability**: FR-001 through FR-011, SC-001 through SC-006, acceptance scenarios 1-5, and DESIGN-REQ-014, DESIGN-REQ-017, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021 from the trusted MM-428 Jira preset brief.  
**Unit Test Plan**: Focused Vitest coverage in `frontend/src/entrypoints/task-create.test.tsx`, `frontend/src/entrypoints/task-detail.test.tsx`, and `frontend/src/entrypoints/tasks-list.test.tsx`.  
**Integration Test Plan**: Rendered React entrypoint tests serve as integration-style coverage for route behavior because backend contracts and persistence are unchanged.

## Phase 1: Setup

- [X] T001 Review `.specify/memory/constitution.md`, `README.md`, `docs/UI/MissionControlDesignSystem.md`, and the trusted MM-428 Jira preset brief in `docs/tmp/jira-orchestration-inputs/MM-428-moonspec-orchestration-input.md`. (FR-011, DESIGN-REQ-014, DESIGN-REQ-017, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021)
- [X] T002 Create MM-428 MoonSpec artifacts under `specs/220-apply-page-composition/`, preserving the trusted Jira preset brief and source design coverage IDs. (FR-011, SC-006)

## Phase 2: Tests First

- [X] T003 Add focused route-composition tests in `frontend/src/entrypoints/task-create.test.tsx` proving matte/satin step cards, one bottom floating launch rail, launch CTA, and matte textareas. (FR-003, FR-004, FR-005, SC-002, SC-003, DESIGN-REQ-020)
- [X] T004 Add focused route-composition tests in `frontend/src/entrypoints/task-detail.test.tsx` proving summary, facts, steps, evidence/logs, artifacts/timeline, and actions are structurally distinct and dense regions are matte/readable. (FR-006, FR-007, FR-008, SC-004, DESIGN-REQ-017, DESIGN-REQ-021)
- [X] T005 Reuse or extend `frontend/src/entrypoints/tasks-list.test.tsx` to preserve task-list control deck/data slab behavior as MM-428 regression evidence. (FR-001, FR-002, SC-001, DESIGN-REQ-014, DESIGN-REQ-019)
- [X] T006 Run focused route-composition tests before production changes and record red-first evidence or verification-only pass state. Red-first evidence: task-detail composition test failed before `.task-detail-page` and evidence-region markers existed; create-page CSS assertion was repaired to match the tokenized textarea implementation before final pass. (FR-010)

## Phase 3: Implementation

- [X] T007 Update `frontend/src/entrypoints/task-detail.tsx` with explicit task-detail composition classes/data attributes for summary, facts, steps, actions, artifacts, timeline, observation/logs, and evidence-heavy regions. (FR-006, FR-007, FR-008)
- [X] T008 Update `frontend/src/styles/mission-control.css` with task-detail/evidence matte-region styling and responsive wrapping safeguards. (FR-007, FR-008, DESIGN-REQ-017, DESIGN-REQ-021)
- [X] T009 Update `frontend/src/entrypoints/task-create.tsx` or `frontend/src/styles/mission-control.css` only if red-first create-page tests expose a real gap. Final action: no create-page markup change was needed; `frontend/src/styles/mission-control.css` now makes step instruction textareas use `--mm-input-well` explicitly for matte/readable styling. (FR-003, FR-004, FR-005)

## Phase 4: Story Validation

- [X] T010 Run direct targeted Vitest for `task-create.test.tsx`, `task-detail.test.tsx`, and `tasks-list.test.tsx`. (FR-001 through FR-010, SC-001 through SC-005)
- [X] T011 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/tasks-list.test.tsx` or document the exact blocker. (FR-009, FR-010)

## Phase 5: Final Verification

- [X] T012 Run final `/moonspec-verify` read-only verification for `specs/220-apply-page-composition/spec.md` and write `specs/220-apply-page-composition/verification.md` with coverage, commands, source traceability, and verdict. (FR-011, SC-006)

## Dependencies And Execution Order

1. Complete setup tasks T001-T002.
2. Write tests first in T003-T005.
3. Run red-first or verification-only evidence in T006.
4. Complete implementation tasks T007-T009 as required by test failures.
5. Run story validation tasks T010-T011.
6. Run final verification in T012.

## Implementation Strategy

Task-list rows are implemented and preserved by MM-426 regression evidence. Create-page rows are now verified by focused MM-428 tests, and task-detail/evidence rows are implemented with explicit composition markers, matte/readable styling, and focused verification coverage.
