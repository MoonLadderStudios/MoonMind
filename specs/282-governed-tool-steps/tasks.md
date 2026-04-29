# Tasks: Governed Tool Step Authoring

**Input**: Design documents from `/specs/282-governed-tool-steps/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Source Traceability**: MM-563; FR-001 through FR-006; SC-001 through SC-004; DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-015.

**Test Commands**:

- Unit tests: `pytest tests/unit/workflows/tasks/test_task_contract.py -q`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify existing test/tooling paths.

- [X] T001 Verify existing Create-page and task contract test files are the implementation targets in `frontend/src/entrypoints/task-create.test.tsx` and `tests/unit/workflows/tasks/test_task_contract.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No new infrastructure is required; the story uses existing Create-page state and task contract validation.

- [X] T002 Confirm `.gitignore` and existing project ignores cover generated test/cache artifacts.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Governed Tool Authoring

**Summary**: As a task author, I want to configure Tool steps as typed governed operations so deterministic work can be submitted with contract-shaped inputs instead of arbitrary script text.

**Independent Test**: Render the Create page, switch a step to Tool, enter a typed Jira tool id, optional version, and JSON inputs, submit the task, and verify the submitted payload contains a Tool step with those fields while invalid JSON, missing tool id, and shell-like fields are rejected before execution.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, SC-001, SC-002, SC-003, SC-004, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-015

**Test Plan**:

- Unit: task contract rejects shell/script/command fields on executable steps.
- Integration: Create page submits valid Tool id/version/inputs and blocks invalid Tool inputs before `/api/executions`.

### Unit Tests (write first)

- [X] T003 [P] Add failing unit test for shell/script/command field rejection in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-005 and SC-003.
- [X] T004 Run `pytest tests/unit/workflows/tasks/test_task_contract.py -q` to confirm T003 fails for the expected reason.

### Integration Tests (write first)

- [X] T005 [P] Add failing Create-page test for valid manual Tool step submission in `frontend/src/entrypoints/task-create.test.tsx` covering FR-001, FR-002, FR-004, SC-001, DESIGN-REQ-003, and DESIGN-REQ-004.
- [X] T006 [P] Add failing Create-page test for invalid Tool inputs blocking submission in `frontend/src/entrypoints/task-create.test.tsx` covering FR-003 and SC-002.
- [X] T007 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` to confirm T005-T006 fail for the expected reason.

### Implementation

- [X] T008 Add Tool draft state fields, parsing, and validation in `frontend/src/entrypoints/task-create.tsx` covering FR-001 and FR-003.
- [X] T009 Submit authored manual Tool steps with `tool.id`, optional `tool.version`, and object `tool.inputs` in `frontend/src/entrypoints/task-create.tsx` covering FR-002 and FR-004.
- [X] T010 Update Tool panel labels/helper copy in `frontend/src/entrypoints/task-create.tsx` covering FR-006 and SC-004.
- [X] T011 Extend task contract forbidden step keys in `moonmind/workflows/tasks/task_contract.py` covering FR-005 and DESIGN-REQ-015.
- [X] T012 Run `pytest tests/unit/workflows/tasks/test_task_contract.py -q` and `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`; fix failures.

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

- [X] T013 Run `./tools/test_unit.sh` for full unit verification or document the exact blocker.
- [X] T014 Run `/moonspec-verify` and write `specs/282-governed-tool-steps/verification.md`.

---

## Dependencies & Execution Order

- Phase 1 before Phase 2.
- T003 and T005-T006 can be authored in parallel.
- T004 and T007 must run before implementation.
- T008-T011 depend on failing tests.
- T012 depends on implementation.
- T013-T014 depend on story tests passing.

## Parallel Example: Story Phase

```bash
pytest tests/unit/workflows/tasks/test_task_contract.py -q
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

## Implementation Strategy

1. Confirm target files and ignores.
2. Add failing unit and frontend tests.
3. Implement Tool draft fields, validation, payload submission, and backend forbidden-key rejection.
4. Run targeted tests, then full unit verification.
5. Run final MoonSpec verification and preserve MM-563 evidence.
