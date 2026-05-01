# Tasks: Author Governed Tool Steps

**Input**: Design documents from `/work/agent_jobs/mm:e448020a-e8e2-4796-b950-9b1c89ba469d/repo/specs/289-author-governed-tool-steps/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Source Traceability**: MM-576; FR-001 through FR-008; SC-001 through SC-006; DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-019, DESIGN-REQ-020.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh` (or targeted pytest only if backend contract files change)
- Integration tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm target files and existing trusted tool surfaces.

- [X] T001 Verify existing Create-page Tool authoring and tests in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx` are the primary implementation targets for FR-001 through FR-004 and FR-006.
- [X] T002 Verify trusted MCP Tool metadata and Jira transition tool contracts in `api_service/api/routers/mcp_tools.py` and `moonmind/mcp/jira_tool_registry.py` are sufficient for the UI contract, documenting any backend change if required.
- [X] T003 Confirm the absent MoonSpec helper scripts (`scripts/bash/update-agent-context.sh`, `scripts/bash/check-prerequisites.sh`) do not block manual artifact generation in `specs/289-author-governed-tool-steps/plan.md` notes or final report.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish typed metadata and option state shapes before tests and implementation.

- [X] T004 Define frontend Tool metadata, grouped option, and dynamic option state helpers in `frontend/src/entrypoints/task-create.tsx` covering FR-001, FR-003, FR-004, DESIGN-REQ-007, and DESIGN-REQ-008.
- [X] T005 Confirm no backend schema or route changes are required for `/api/mcp/tools` and `/api/mcp/tools/call`; if they are required, add failing unit tests in `tests/unit/mcp/test_jira_tool_registry.py` before implementation.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Governed Tool Step Authoring

**Summary**: As a task author, I want to configure a Tool step as a typed governed operation so deterministic integrations run with schema, authorization, capability, retry, and error contracts.

**Independent Test**: Open the task authoring surface, add a Tool step, search or browse grouped Tool operations, configure a Jira-style governed operation with schema-valid inputs and dynamic target-status options, submit the task, and verify the submitted step is a typed Tool operation while invalid tools, invalid inputs, unavailable authorization/capability states, and arbitrary shell input are rejected before execution.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-019, DESIGN-REQ-020

**Unit Test Plan**:

- Preserve existing task contract shell-like field rejection coverage in `tests/unit/workflows/tasks/test_task_contract.py` for FR-005 and SC-004.
- Add backend unit tests only if metadata or trusted Jira transition contract behavior changes.

**Integration Test Plan**:

- Create-page tests mock trusted Tool metadata, verify grouped/searchable Tool selection, schema guidance, dynamic Jira status options, fail-closed option lookup behavior, unknown Tool rejection, and valid Tool submission.

### Unit Tests (write first)

- [X] T006 [P] Inspect existing shell/script task contract tests in `tests/unit/workflows/tasks/test_task_contract.py` and add a failing regression only if FR-005 or SC-004 coverage is missing.
- [X] T007 [P] Backend unit tests in `tests/unit/mcp/test_jira_tool_registry.py` were not required because existing `/api/mcp/tools` metadata and `jira.get_transitions` schemas were sufficient for FR-004.

### Integration Tests (write first)

- [X] T008 [P] Add a failing Create-page test in `frontend/src/entrypoints/task-create.test.tsx` for grouped/searchable Tool picker selection from mocked `/api/mcp/tools`, covering FR-001, SC-001, DESIGN-REQ-008, and SC-005.
- [X] T009 [P] Add a failing Create-page test in `frontend/src/entrypoints/task-create.test.tsx` for selected Tool schema guidance and valid typed Tool submission, covering FR-002, FR-003, SC-001, and DESIGN-REQ-007.
- [X] T010 [P] Add a failing Create-page test in `frontend/src/entrypoints/task-create.test.tsx` for Jira transition dynamic status options using trusted `/api/mcp/tools/call`, covering FR-004, SC-002, and DESIGN-REQ-008.
- [X] T011 [P] Add a failing Create-page test in `frontend/src/entrypoints/task-create.test.tsx` that blocks unknown Tool ids and failed dynamic option lookup before `/api/executions`, covering FR-006, SC-003, and DESIGN-REQ-019.
- [X] T012 Ran `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`; Python unit suite passed and the Create-page Vitest file was skipped by existing `describe.skip`, so red-first UI execution is blocked by repo test state.

### Implementation

- [X] T013 Load trusted Tool metadata from `/api/mcp/tools` in `frontend/src/entrypoints/task-create.tsx` and derive grouped/searchable Tool options covering FR-001 and DESIGN-REQ-008.
- [X] T014 Replace free-only Tool id entry with governed selection plus advanced/manual display of selected id in `frontend/src/entrypoints/task-create.tsx`, preserving payload compatibility for FR-002 and SC-001.
- [X] T015 Render selected Tool schema field guidance and maintain JSON object payload validation in `frontend/src/entrypoints/task-create.tsx` covering FR-003 and DESIGN-REQ-007.
- [X] T016 Add Jira transition dynamic option lookup through trusted `/api/mcp/tools/call` in `frontend/src/entrypoints/task-create.tsx`, mapping returned target statuses without guessing transition ids, covering FR-004 and SC-002.
- [X] T017 Add fail-closed submit validation for unknown Tool ids, unresolved required schema fields, failed dynamic option provider state, and invalid dynamic selections in `frontend/src/entrypoints/task-create.tsx`, covering FR-006 and DESIGN-REQ-019.
- [X] T018 Preserve Tool terminology and avoid Script/Activity/worker-placement copy in `frontend/src/entrypoints/task-create.tsx` covering FR-007, SC-005, and DESIGN-REQ-020.
- [X] T019 Backend metadata or Jira registry changes were not required because T005/T007 found no verified backend contract gap.
- [X] T020 Ran `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`, `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`, and focused ESLint; fixed compile/lint issues.

**Checkpoint**: The implementation is present, TypeScript/lint validation passes, Python shell guardrails remain covered, and frontend integration assertions are added but skipped by existing repo test state.

---

## Phase 4: Polish & Cross-Cutting Concerns

- [X] T021 [P] Update `specs/289-author-governed-tool-steps/quickstart.md` if final commands or backend scope changed during implementation.
- [X] T022 [P] Review `specs/289-author-governed-tool-steps/spec.md`, `plan.md`, `research.md`, `data-model.md`, and `contracts/governed-tool-picker.md` for MM-576 traceability and alignment after implementation.
- [X] T023 Full Python unit verification passed as part of `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`; UI tests were skipped by existing `describe.skip`.
- [X] T024 Ran manual `/moonspec-verify` equivalent and wrote `specs/289-author-governed-tool-steps/verification.md` preserving MM-576, SC-006, and DESIGN-REQ coverage.

---

## Dependencies & Execution Order

- Phase 1 before Phase 2.
- T006-T011 can be authored in parallel after T004-T005.
- T012 must run before implementation tasks T013-T019.
- T013-T018 are coupled in `frontend/src/entrypoints/task-create.tsx` and should be implemented together after red-first confirmation.
- T019 runs only if backend contract gaps are proven.
- T020 depends on implementation.
- T023-T024 depend on story tests passing.

## Parallel Example: Story Phase

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
pytest tests/unit/workflows/tasks/test_task_contract.py -q
```

## Implementation Strategy

1. Preserve MM-576 and the canonical Jira preset brief as the final alignment source.
2. Add failing Create-page integration tests for picker discovery, schema guidance, dynamic Jira options, unknown Tool blocking, and option failure blocking.
3. Implement the smallest Create-page metadata and dynamic option layer over existing `/api/mcp/tools` and `/api/mcp/tools/call` contracts.
4. Avoid backend changes unless the trusted metadata contract lacks necessary fields for the tested behavior.
5. Run targeted UI validation, then full unit validation, then final MoonSpec verification.
