# Tasks: Author Governed Tool Steps

**Input**: Design documents from `/specs/289-author-governed-tool-steps/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Source Traceability**: MM-576; FR-001 through FR-008; SC-001 through SC-005; DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-019, DESIGN-REQ-020.

**Test Commands**:

- Unit tests: `pytest tests/unit/workflows/tasks/test_task_contract.py -q`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm existing implementation targets and generated artifact scope.

- [X] T001 Inspect existing Tool step authoring tests and implementation in `frontend/src/entrypoints/task-create.test.tsx` and `frontend/src/entrypoints/task-create.tsx` for FR-001 through FR-008.
- [X] T002 Inspect existing backend executable-step contract tests in `tests/unit/workflows/tasks/test_task_contract.py` for SC-004 and DESIGN-REQ-020.

---

## Phase 2: Foundational

**Purpose**: No new backend infrastructure is required; the story uses existing trusted MCP tool endpoints.

- [X] T003 Confirm `/mcp/tools` and `/mcp/tools/call` are the existing trusted tool discovery/call contracts in `specs/289-author-governed-tool-steps/contracts/governed-tool-authoring-ui.md`.

**Checkpoint**: Foundation ready - story test and implementation work can begin.

---

## Phase 3: Story - Governed Tool Selection

**Summary**: As a task author, I can configure a Tool step through a governed picker so deterministic integrations run with visible contracts and bounded schema-shaped inputs.

**Independent Test**: Render the Create page, switch a step to Tool, search grouped Jira tools returned by the trusted tool discovery endpoint, select `jira.transition_issue`, enter an issue key, choose a target status loaded through the trusted transitions tool, submit the task, and verify the submitted Tool payload includes the selected tool id and schema-shaped inputs without arbitrary shell/script fields.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-019, DESIGN-REQ-020

**Test Plan**:

- Unit: rerun task contract tests that reject conflicting Tool/Skill payloads and shell-like fields.
- Integration: Create page tests for grouped/searchable Tool discovery, Jira transition status dynamic options, fallback behavior, and final Tool payload.

### Tests First

- [X] T004 [P] Add failing Create-page integration test for grouped/searchable trusted Tool choices in `frontend/src/entrypoints/task-create.test.tsx` covering FR-001, FR-003, FR-004, SC-001, DESIGN-REQ-008.
- [X] T005 [P] Add failing Create-page integration test for `jira.transition_issue` dynamic target statuses and submitted Tool payload in `frontend/src/entrypoints/task-create.test.tsx` covering FR-005, FR-006, FR-008, SC-002, DESIGN-REQ-007, DESIGN-REQ-008.
- [X] T006 [P] Add failing Create-page integration test for discovery/dynamic-option failure fallback in `frontend/src/entrypoints/task-create.test.tsx` covering FR-002, SC-003.
- [X] T007 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` to confirm T004-T006 fail for the expected missing UI behavior.

### Implementation

- [X] T008 Add trusted Tool discovery state, fetch handling, search grouping, and failure fallback in `frontend/src/entrypoints/task-create.tsx` covering FR-001, FR-002, FR-003, FR-004.
- [X] T009 Add visible Tool contract metadata copy without Script terminology in `frontend/src/entrypoints/task-create.tsx` covering FR-007, DESIGN-REQ-007, DESIGN-REQ-019, DESIGN-REQ-020.
- [X] T010 Add Jira transition dynamic option loading and JSON update behavior in `frontend/src/entrypoints/task-create.tsx` covering FR-005, FR-006, DESIGN-REQ-008.
- [X] T011 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and fix failures.

**Checkpoint**: The story is fully functional and independently testable.

---

## Phase 4: Polish & Cross-Cutting Concerns

- [X] T012 Run `pytest tests/unit/workflows/tasks/test_task_contract.py -q` for SC-004 and DESIGN-REQ-020.
- [X] T013 Run `./tools/test_unit.sh` for final unit verification or document the exact blocker.
- [X] T014 Run `/moonspec-verify` and write `specs/289-author-governed-tool-steps/verification.md`.

---

## Dependencies & Execution Order

- Phase 1 before Phase 2.
- T004, T005, and T006 can be authored in parallel.
- T007 must run before implementation.
- T008 through T010 depend on failing tests.
- T011 depends on implementation.
- T012 through T014 depend on story tests passing.

## Parallel Example

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
pytest tests/unit/workflows/tasks/test_task_contract.py -q
```

## Implementation Strategy

1. Preserve existing manual Tool authoring and submission behavior.
2. Add discovery/grouping/search tests and implementation.
3. Add trusted Jira transition option tests and implementation.
4. Rerun focused frontend and contract tests.
5. Run final unit verification and MoonSpec verification.
