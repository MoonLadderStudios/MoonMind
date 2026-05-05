# Tasks: Mobile, Accessibility, and Live-Update Stability

**Input**: `specs/304-mobile-accessibility-live-update-stability/spec.md`  
**Plan**: `specs/304-mobile-accessibility-live-update-stability/plan.md`  
**Unit Test Command**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`  
**Integration Test Command**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`

**Source Traceability**: The original `MM-591` Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-010, acceptance scenarios 1 through 7, SC-001 through SC-006, and DESIGN-REQ-006, DESIGN-REQ-021, DESIGN-REQ-022, and DESIGN-REQ-023.

## Phase 1: Setup

- [X] T001 Confirm no existing `MM-591` MoonSpec artifact exists and assign global next spec prefix `304` under `specs/`.
- [X] T002 Create `specs/304-mobile-accessibility-live-update-stability/` artifact structure and preserve the canonical Jira preset brief in `spec.md`.

## Phase 2: Foundational

- [X] T003 Inspect `docs/UI/TasksListPage.md` sections 5.7, 14, 15, and 16 for source design requirements. (DESIGN-REQ-006, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023)
- [X] T004 Inspect current Tasks List implementation and tests in `frontend/src/entrypoints/tasks-list.tsx` and `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-001 through FR-009)

## Phase 3: Story - Stable Accessible Task Filters

**Story Summary**: Operators on mobile, desktop, and assistive technologies can use equivalent task filters without live updates disrupting staged selections.

**Independent Test**: Render the Tasks List page with mocked execution data, operate desktop filter dialogs by keyboard, operate mobile filters, and verify task-scoped API URLs and focus behavior.

**Traceability IDs**: FR-001 through FR-010; SC-001 through SC-006; DESIGN-REQ-006, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023.

### Tests

- [X] T005 Extend the mobile filter reachability test in `frontend/src/entrypoints/tasks-list.test.tsx` to cover ID and Title controls plus task-scoped URL serialization. (FR-001, FR-002, SC-001, SC-002, DESIGN-REQ-023)
- [X] T006 Add a focused UI test in `frontend/src/entrypoints/tasks-list.test.tsx` proving desktop filter dialogs focus the first control, apply staged text filters with Enter, and return focus to the originating control. (FR-004, FR-006, SC-003, SC-004, DESIGN-REQ-022)
- [X] T007 Preserve existing staging, Escape, outside-click, active-chip, workflow-kind, and mobile-card tests in `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-003, FR-005, FR-008, FR-009, DESIGN-REQ-006, DESIGN-REQ-021, DESIGN-REQ-022)

### Implementation

- [X] T008 Add `taskId` and `title` text filters to `ColumnFilters` parsing, URL serialization, summaries, active chips, desktop filter popovers, and mobile controls in `frontend/src/entrypoints/tasks-list.tsx`. (FR-001, FR-002, DESIGN-REQ-023)
- [X] T009 Add desktop filter focus management and Enter-to-apply handling in `frontend/src/entrypoints/tasks-list.tsx`. (FR-004, FR-006, DESIGN-REQ-022)
- [X] T010 Pause Tasks List live refetch intervals while a desktop filter editor is open in `frontend/src/entrypoints/tasks-list.tsx`. (FR-007, DESIGN-REQ-021)
- [X] T011 Preserve task-only scope enforcement and existing workflow-kind fail-safe behavior in `frontend/src/entrypoints/tasks-list.tsx`. (FR-009)

### Validation

- [X] T012 Run focused UI validation: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`. (FR-001 through FR-010)
- [X] T013 Run final unit validation: `./tools/test_unit.sh`. (FR-001 through FR-010)
- [X] T014 Produce final MoonSpec verification report in `specs/304-mobile-accessibility-live-update-stability/verification.md`. (SC-006)

## Dependencies and Execution Order

1. T001-T004 establish artifacts and source understanding.
2. T005-T007 define and preserve test coverage.
3. T008-T011 implement the bounded UI changes.
4. T012-T014 validate and publish final evidence.

## Parallel Examples

- T005 and T006 can be drafted independently because they cover different test scenarios in the same file, but final edits must be merged carefully.
- T008 and T009 both touch `tasks-list.tsx`, so they should be applied serially.

## Implementation Strategy

The story is mostly implemented by existing Tasks List column-filter work. The remaining runtime work is narrowly scoped to missing text filters, focus/keyboard dialog behavior, and live-update stability while an editor is open.
