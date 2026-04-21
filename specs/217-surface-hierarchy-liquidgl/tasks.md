# Tasks: Surface Hierarchy and liquidGL Fallback Contract

**Input**: Design documents from `/specs/217-surface-hierarchy-liquidgl/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style frontend tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Source Traceability**: Tasks cover FR-001 through FR-010, SC-001 through SC-005, and DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-015, DESIGN-REQ-018, and DESIGN-REQ-027.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/tasks-list.test.tsx`
- Integration tests: Focused frontend integration-style assertions in the same Vitest suites
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Create MoonSpec artifacts for MM-425 in `specs/217-surface-hierarchy-liquidgl/`
- [X] T002 Preserve canonical Jira input in `docs/tmp/jira-orchestration-inputs/MM-425-moonspec-orchestration-input.md` and `specs/217-surface-hierarchy-liquidgl/spec.md`

## Phase 2: Foundational

- [X] T003 Confirm implementation target is the shared Mission Control stylesheet and existing frontend suites in `frontend/src/styles/mission-control.css` and `frontend/src/entrypoints/*`

## Phase 3: Story - Surface Hierarchy and Fallbacks

**Summary**: As a Mission Control operator, I want content surfaces, control surfaces, and premium liquid-glass surfaces to have distinct roles and reliable fallbacks so dense work stays readable while elevated controls feel premium.

**Independent Test**: Inspect CSS and render representative task-list/Create page surfaces to confirm hierarchy roles, fallback rules, opt-in liquidGL, dense readability, and unchanged behavior.

**Traceability**: FR-001 through FR-010, SC-001 through SC-005, DESIGN-REQ-003/004/005/007/008/015/018/027

### Unit Tests (write first)

- [X] T004 Add failing CSS contract tests for all surface roles and glass fallback rules in `frontend/src/entrypoints/mission-control.test.tsx` (FR-001, FR-002, FR-003, SC-001, SC-002)
- [X] T005 Add failing CSS contract tests for opt-in liquidGL and dense-surface exclusions in `frontend/src/entrypoints/mission-control.test.tsx` (FR-004, FR-005, FR-007, FR-008, SC-003)
- [X] T006 Run focused UI tests and confirm the new MM-425 assertions fail before implementation

### Integration Tests (write first)

- [X] T007 Preserve task-list control/data slab behavior in `frontend/src/entrypoints/tasks-list.test.tsx` (FR-006, DESIGN-REQ-027, SC-004)
- [X] T008 Preserve Create page liquidGL target controls in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, FR-007, SC-004)

### Implementation

- [X] T009 Implement semantic surface roles and fallback rules in `frontend/src/styles/mission-control.css` (FR-001, FR-002, FR-003, DESIGN-REQ-005, DESIGN-REQ-007)
- [X] T010 Implement dense/nested/satin readability protections in `frontend/src/styles/mission-control.css` (FR-005, FR-006, DESIGN-REQ-003, DESIGN-REQ-008, DESIGN-REQ-027)
- [X] T011 Implement explicit liquidGL hero shell/fallback styling in `frontend/src/styles/mission-control.css` (FR-004, FR-007, FR-008, DESIGN-REQ-018)
- [X] T012 Run focused UI tests and update implementation until they pass

## Phase 4: Polish and Verification

- [X] T013 Run full `./tools/test_unit.sh`
- [X] T014 Run `/moonspec-verify` and record result in `specs/217-surface-hierarchy-liquidgl/verification.md`

## Dependencies & Execution Order

- T004-T005 must be written before T009-T011.
- T006 must confirm failing MM-425 assertions before implementation.
- T009-T011 must complete before T012.
- T013-T014 are final gates after focused tests pass.
